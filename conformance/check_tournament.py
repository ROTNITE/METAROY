#!/usr/bin/env python3
"""Чекер технического допуска к перекрёстному турниру хакатона «Метарой 1.0».

Без внешних зависимостей (только стандартная библиотека Python 3.9+).

ЧЕКЕР НЕ ЗАПУСКАЕТ ВАШИ ПРОЦЕССЫ. Сначала поднимите свои env и ctrl по README,
затем проверьте, что health отвечает, и только потом запускайте этот скрипт.

Быстрый старт:
  1. python3 check_tournament.py selftest \
       --env-url http://127.0.0.1:8000 --ctrl-url http://127.0.0.1:8100 --max-steps 10
  2. Смотрите FAIL/WARN и блок «Что исправить».
     FAIL=0 - технический допуск, --strict - WARN тоже обязателен.
  3. Для устойчивости повторите на нескольких --seed (42, 777, 123456).

Основные режимы:
  python3 check_tournament.py env   --url http://127.0.0.1:8000
  python3 check_tournament.py ctrl  --url http://127.0.0.1:8100
  python3 check_tournament.py pair  --env-url http://127.0.0.1:8000 --ctrl-url http://127.0.0.1:8100 --normalize

Проверка одной стороны без ожидания чужой команды:
  python3 check_tournament.py pair --ctrl-url http://127.0.0.1:8100 --ref-env --normalize
  python3 check_tournament.py pair --env-url  http://127.0.0.1:8000 --ref-ctrl --normalize

Эталонные ref-env/ref-ctrl - тестовые двойники, а не турнирные решения.
Полные требования и чек-листы - в REGULATION_TECH.md.
"""

import argparse
import copy
import http.server
import json
import math
import random
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

# Не падать на консолях Windows, не поддерживающих отдельные Unicode-символы.
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

PROTOCOL_VERSION = "1.0"
META_OPERATIONS = ("keep", "select", "switch", "modify", "compose", "synthesize", "safe_stop")
STANDARD_CHECK_STATUSES = {"passed", "failed", "unknown"}
REQUIRED_OBS_KEYS = (
    "protocol_version", "episode_id", "request_id", "time", "mission", "agents",
    "infrastructure", "targets", "zones", "network", "events", "constraints",
    "metrics", "observation_quality",
)
ERROR_CODES = {
    "INVALID_REQUEST", "UNSUPPORTED_PROTOCOL_VERSION", "UNKNOWN_EPISODE",
    "INVALID_COMMAND", "CONSTRAINT_VIOLATION", "INVALID_SCENARIO",
    "RESOURCE_EXHAUSTED", "TIMEOUT", "INTERNAL_ERROR",
}
FALLBACK_SCENARIOS = (
    "coverage_search", "priority_task", "relay_coalition",
    "safe_degradation", "coordination_reconfiguration",
)

# Нормативные наборы полей (Табл. 3, 4, 5, 6, 9 и Listing 6 Приложения А).
OBS_TOP_FIELDS = set(REQUIRED_OBS_KEYS)
OBS_MISSION_FIELDS = {"id", "objectives", "priorities", "directives", "constraints", "status", "termination"}
OBS_AGENT_FIELDS = {
    "id", "kind", "position", "orientation", "status", "capabilities", "resources",
    "communication", "role", "scenario_ids", "available_actions", "constraints", "trust",
}
OBS_INFRA_FIELDS = {
    "id", "kind", "position", "status", "capabilities", "resources", "communication", "constraints",
}
OBS_ZONE_FIELDS = {"id", "geometry", "type", "access", "risk", "cost", "communication", "constraints"}
OBS_NETWORK_FIELDS = {
    "connectivity_score", "partition_count", "reachable_agent_ratio",
    "average_link_quality", "mesh_available", "known_links",
}
OBS_QUALITY_FIELDS = {
    "global_completeness", "maximum_delay", "stale_data_ratio",
    "contradiction_count", "unknown_state_ratio",
}
OBS_EVENT_FIELDS = {"id", "type", "time", "source", "targets", "parameters", "confidence", "status"}

DECISION_FIELDS = {
    "protocol_version", "episode_id", "decision_id", "request_id", "time", "operation",
    "previous_configuration", "selected_configuration", "candidate_evaluations",
    "role_assignments", "commands", "constraint_checks", "explanation",
}

# Минимальная декларация synthesize (Listing 3 Приложения А).
MIN_SYNTH_SCENARIO = {
    "id": "tournament_synth_01",
    "version": "1.0",
    "title": "Tournament synthesized scenario",
    "game_model": {
        "class": "coalition",
        "players": ["scout", "executor"],
        "information_structure": "partial",
        "stability_criterion": "utility_non_decreasing",
    },
    "applicability": {"entry": [], "continuation": [], "exit": [], "emergency_exit": []},
    "roles": ["scout", "executor"],
    "objectives": ["search", "rescue"],
    "constraints": [],
    "allowed_actions": ["move", "observe", "relay"],
    "transition_targets": [],
    "metrics": ["mission_completion", "safety_violations"],
}

DEFAULT_MIN_STEPS = 5

# Уникальный тег запуска, чтобы повторные запуски чекера не попадали в кэш
# идемпотентности по request_id/decision_id уже запущенной среды.
RUN_TAG = uuid.uuid4().hex[:10]


def http_json(base_url, method, path, payload=None, timeout=10.0):
    url = urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, _try_json(raw), raw, time.monotonic() - t0
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return exc.code, _try_json(raw), raw, time.monotonic() - t0
    except Exception as exc:
        return 0, None, str(exc), time.monotonic() - t0


def _try_json(raw):
    try:
        return json.loads(raw)
    except Exception:
        return None


def unwrap_obs(payload):
    if isinstance(payload, dict) and isinstance(payload.get("observation"), dict):
        return payload["observation"]
    return payload if isinstance(payload, dict) else {}


def action_name(item):
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return item.get("action") or item.get("id") or item.get("name")
    return None


def _is_num(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def finite_vec3(v):
    return isinstance(v, list) and len(v) == 3 and all(_is_num(x) for x in v)


class PublicNormalizer:
    """Нормализация наблюдения по п. 1.18 Приложения А.

    Удаляет extensions и недокументированные поля, переименовывает ID сущностей
    и их ссылки (не трогая служебные поля схемы), перемешивает массивы.
    """

    # Служебные ключи, значения которых НИКОГДА не переименовываются, даже если
    # какой-то ID в среде случайно совпал со словом вроде "air", "land" или "relay".
    PROTECTED_KEYS = {
        "protocol_version", "episode_id", "request_id", "decision_id",
        "kind", "type", "status", "action", "capabilities", "available_actions",
        "operation", "unit", "role", "scenario_ids", "priorities",
        "supported_operations", "constraint_id", "reason_codes", "expected_effects",
        "class", "information_structure", "stability_criterion", "version", "title",
        "allowed_actions", "metrics",
    }

    def __init__(self, seed=42, strip_extensions=True, strip_unknown=True,
                 rename_ids=True, shuffle=True):
        self.rng = random.Random(seed)
        self.strip_extensions = strip_extensions
        self.strip_unknown = strip_unknown
        self.rename_ids = rename_ids
        self.shuffle = shuffle
        self.orig_to_norm = {}
        self.norm_to_orig = {}
        self._counters = {}

    def _alias(self, prefix, orig_id):
        if not self.rename_ids or not isinstance(orig_id, str):
            return orig_id
        if orig_id not in self.orig_to_norm:
            n = self._counters.get(prefix, 0) + 1
            self._counters[prefix] = n
            norm = f"{prefix}_{n:03d}"
            self.orig_to_norm[orig_id] = norm
            self.norm_to_orig[norm] = orig_id
        return self.orig_to_norm[orig_id]

    @staticmethod
    def _filter_dict(value, allowed):
        if not isinstance(value, dict):
            return value
        return {k: v for k, v in value.items() if k in allowed}

    def _strip_unknown(self, obs):
        for key in list(obs.keys()):
            if key not in OBS_TOP_FIELDS:
                obs.pop(key, None)
        mission = obs.get("mission")
        if isinstance(mission, dict):
            obs["mission"] = self._filter_dict(mission, OBS_MISSION_FIELDS)
        for field, allowed in (
            ("agents", OBS_AGENT_FIELDS),
            ("infrastructure", OBS_INFRA_FIELDS),
            ("zones", OBS_ZONE_FIELDS),
        ):
            items = obs.get(field)
            if isinstance(items, list):
                obs[field] = [self._filter_dict(x, allowed) if isinstance(x, dict) else x for x in items]
        network = obs.get("network")
        if isinstance(network, dict):
            obs["network"] = self._filter_dict(network, OBS_NETWORK_FIELDS)
        quality = obs.get("observation_quality")
        if isinstance(quality, dict):
            obs["observation_quality"] = self._filter_dict(quality, OBS_QUALITY_FIELDS)
        events = obs.get("events")
        if isinstance(events, list):
            obs["events"] = [self._filter_dict(x, OBS_EVENT_FIELDS) if isinstance(x, dict) else x for x in events]

    def _map_ids_safe(self, value, table, parent_key=None):
        """Рекурсивно заменяет точные совпадения ID, пропуская служебные поля схемы."""
        if parent_key in self.PROTECTED_KEYS:
            return value
        if isinstance(value, str):
            return table.get(value, value)
        if isinstance(value, list):
            return [self._map_ids_safe(x, table, parent_key=None) for x in value]
        if isinstance(value, dict):
            # Не трогаем id ресурсов/миссии/ограничений, только ссылки на сущности.
            is_resource_obj = (
                "value" in value and "unit" in value and value.get("unit") == "normalized"
            )
            out = {}
            for k, v in value.items():
                if is_resource_obj and k == "id":
                    out[k] = v
                elif k == "resources" and isinstance(v, dict):
                    out[k] = v
                else:
                    out[k] = self._map_ids_safe(v, table, parent_key=k)
            return out
        return value

    def _map_role_keys(self, holder):
        if isinstance(holder, dict) and isinstance(holder.get("role_assignments"), dict):
            holder["role_assignments"] = {
                self.norm_to_orig.get(k, k): v for k, v in holder["role_assignments"].items()
            }

    def _shuffle_list(self, parent, key):
        value = parent.get(key) if isinstance(parent, dict) else None
        if isinstance(value, list) and len(value) > 1:
            self.rng.shuffle(value)

    def forward_obs(self, obs):
        out = copy.deepcopy(obs)
        if self.strip_extensions:
            out.pop("extensions", None)
        if self.strip_unknown:
            self._strip_unknown(out)

        for field, prefix in (
            ("agents", "ag"), ("infrastructure", "inf"),
            ("targets", "tgt"), ("zones", "zn"),
        ):
            items = out.get(field)
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    item["id"] = self._alias(prefix, item["id"])

        if self.rename_ids and self.orig_to_norm:
            # Переименовываем ссылки, не меняя mission.id и служебные поля.
            mission_id = out["mission"]["id"] if isinstance(out.get("mission"), dict) and "id" in out["mission"] else None
            out = self._map_ids_safe(out, self.orig_to_norm)
            if mission_id is not None and isinstance(out.get("mission"), dict):
                out["mission"]["id"] = mission_id

        if self.shuffle:
            for field in ("agents", "infrastructure", "targets", "zones", "events", "constraints"):
                self._shuffle_list(out, field)
            mission = out.get("mission")
            if isinstance(mission, dict):
                self._shuffle_list(mission, "directives")
                self._shuffle_list(mission, "objectives")
            network = out.get("network")
            if isinstance(network, dict):
                self._shuffle_list(network, "known_links")
        return out

    def backward_decision(self, decision):
        out = copy.deepcopy(decision)
        if not self.rename_ids:
            return out
        for holder in (out, out.get("previous_configuration"), out.get("selected_configuration")):
            self._map_role_keys(holder)
        cmds = out.get("commands")
        if isinstance(cmds, list):
            out["commands"] = self._map_ids_safe(cmds, self.norm_to_orig)
        selected = out.get("selected_configuration")
        if isinstance(selected, dict):
            for key in ("parameters", "synthesized_scenario"):
                if key in selected:
                    selected[key] = self._map_ids_safe(selected[key], self.norm_to_orig)
        return out


class Report:
    def __init__(self, title, strict=False):
        self.title = title
        self.strict = strict
        self.items = []

    def add(self, name, status, detail="", fix=""):
        self.items.append({"check": name, "status": status, "detail": detail, "fix": fix})

    def ok(self, name, detail="", fix=""):
        self.add(name, "PASS", detail, fix)

    def warn(self, name, detail="", fix=""):
        self.add(name, "WARN", detail, fix)

    def fail(self, name, detail="", fix=""):
        self.add(name, "FAIL", detail, fix)

    @property
    def failed(self):
        return sum(1 for x in self.items if x["status"] == "FAIL")

    @property
    def warned(self):
        return sum(1 for x in self.items if x["status"] == "WARN")

    @property
    def passed(self):
        return sum(1 for x in self.items if x["status"] == "PASS")

    @property
    def admitted(self):
        return self.failed == 0 and (not self.strict or self.warned == 0)

    def fixes(self):
        result = []
        seen = set()
        for x in self.items:
            if x["status"] in ("FAIL", "WARN") and x.get("fix") and x["fix"] not in seen:
                seen.add(x["fix"])
                result.append(x["fix"])
        return result

    def print_text(self):
        print(f"\n=== {self.title} ===")
        for x in self.items:
            tag = x["status"].ljust(4)
            det = f" - {x['detail']}" if x["detail"] else ""
            print(f"[{tag}] {x['check']}{det}")
            if x.get("fix") and x["status"] in ("FAIL", "WARN"):
                print(f"       -> {x['fix']}")
        fixes = self.fixes()
        if fixes:
            print("\nЧто исправить:")
            for i, fix in enumerate(fixes, 1):
                print(f"  {i}. {fix}")
        verdict = "ДОПУЩЕН" if self.admitted else "НЕ ДОПУЩЕН"
        extra = " (strict: WARN считается FAIL)" if self.strict else ""
        print(f"\nИтог: {verdict}{extra} (PASS={self.passed}, WARN={self.warned}, FAIL={self.failed})")

    def to_dict(self):
        return {
            "title": self.title,
            "strict": self.strict,
            "admitted": self.admitted,
            "pass": self.passed,
            "warn": self.warned,
            "fail": self.failed,
            "checks": self.items,
            "fixes": self.fixes(),
        }


def make_clean_decision(ep_id, step_num, sim_time, operation, scenario_ids, prev_ids=None,
                        roles=None, prev_roles=None, commands=None, params=None, synth=None):
    return {
        "protocol_version": PROTOCOL_VERSION,
        "episode_id": ep_id,
        "decision_id": f"chk_{RUN_TAG}_dec_{step_num:04d}",
        "request_id": f"chk_{RUN_TAG}_req_{step_num:04d}",
        "time": sim_time,
        "operation": operation,
        "previous_configuration": {
            "scenario_ids": list(prev_ids or []),
            "role_assignments": dict(prev_roles or {}),
        },
        "selected_configuration": {
            "scenario_ids": list(scenario_ids),
            "parameters": dict(params or {}),
            "synthesized_scenario": synth,
        },
        "candidate_evaluations": [
            {
                "scenario_id": scenario_ids[0] if scenario_ids else "coverage_search",
                "applicable": True,
                "utility": 0.8,
                "risk": 0.1,
                "selected": True,
            }
        ],
        "role_assignments": dict(roles or {}),
        "commands": list(commands or []),
        "constraint_checks": [{"constraint_id": "basic_safety", "status": "passed"}],
        "explanation": {
            "observation_refs": [f"obs_{step_num}"],
            "reason_codes": [f"tournament_check_{operation}"],
            "expected_effects": ["protocol_verification"],
            "confidence": 0.9,
        },
    }


def validate_observation(obs):
    """Проверяет обязательные поля публичного наблюдения (Табл. 3, 4, 5, 6, 9, Listing 6)."""
    problems = []
    warnings = []
    if not isinstance(obs, dict):
        return ["observation не является JSON-объектом"], warnings
    missing = [k for k in REQUIRED_OBS_KEYS if k not in obs]
    if missing:
        problems.append(f"нет обязательных полей: {missing}")
    if obs.get("protocol_version") != PROTOCOL_VERSION:
        problems.append(f"observation.protocol_version={obs.get('protocol_version')!r}, ожидается '1.0'")
    if not isinstance(obs.get("episode_id"), str):
        problems.append("observation.episode_id должен быть строкой")
    if not _is_num(obs.get("time")):
        problems.append("observation.time должен быть числом")
    mission = obs.get("mission")
    if not isinstance(mission, dict):
        problems.append("observation.mission должен быть объектом")
    else:
        miss = [k for k in OBS_MISSION_FIELDS if k not in mission]
        if miss:
            problems.append(f"mission: нет полей {miss}")
        for key in ("objectives", "directives", "constraints"):
            if key in mission and not isinstance(mission.get(key), list):
                problems.append(f"mission.{key} должен быть массивом")
    agents = obs.get("agents")
    if not isinstance(agents, list):
        problems.append("observation.agents должен быть массивом")
    else:
        for i, agent in enumerate(agents[:32]):
            if not isinstance(agent, dict):
                problems.append(f"agents[{i}] не объект")
                continue
            miss = [k for k in ("id", "kind", "status", "capabilities", "resources",
                                "communication", "role", "scenario_ids",
                                "available_actions", "constraints") if k not in agent]
            if miss:
                problems.append(f"agents[{i}] ({agent.get('id')}): нет полей {miss}")
            if agent.get("kind") not in ("digital", "operator") and "position" not in agent:
                problems.append(f"agents[{i}] ({agent.get('id')}): нет position для пространственного агента")
            if "position" in agent and agent["position"] is not None and not finite_vec3(agent["position"]):
                problems.append(f"agents[{i}] ({agent.get('id')}): position не [x,y,z]")
            if not isinstance(agent.get("available_actions"), list):
                problems.append(f"agents[{i}] ({agent.get('id')}): available_actions должен быть массивом")
            if not isinstance(agent.get("constraints"), list):
                problems.append(f"agents[{i}] ({agent.get('id')}): constraints должен быть массивом")
    infra = obs.get("infrastructure")
    if not isinstance(infra, list):
        problems.append("observation.infrastructure должен быть массивом")
    else:
        for i, item in enumerate(infra[:32]):
            if not isinstance(item, dict):
                problems.append(f"infrastructure[{i}] не объект")
                continue
            miss = [k for k in OBS_INFRA_FIELDS if k not in item]
            if miss:
                problems.append(f"infrastructure[{i}] ({item.get('id')}): нет полей {miss}")
    zones = obs.get("zones")
    if not isinstance(zones, list):
        problems.append("observation.zones должен быть массивом")
    else:
        for i, zone in enumerate(zones[:64]):
            if not isinstance(zone, dict):
                problems.append(f"zones[{i}] не объект")
                continue
            miss = [k for k in OBS_ZONE_FIELDS if k not in zone]
            if miss:
                problems.append(f"zones[{i}] ({zone.get('id')}): нет полей {miss}")
            if not isinstance(zone.get("geometry"), dict):
                problems.append(f"zones[{i}] ({zone.get('id')}): geometry должен быть объектом")
    network = obs.get("network")
    if not isinstance(network, dict):
        problems.append("observation.network должен быть объектом")
    else:
        miss = [k for k in OBS_NETWORK_FIELDS if k not in network]
        if miss:
            problems.append(f"network: нет полей {miss}")
    events = obs.get("events")
    if not isinstance(events, list):
        problems.append("observation.events должен быть массивом")
    else:
        for i, event in enumerate(events[:32]):
            if not isinstance(event, dict):
                problems.append(f"events[{i}] не объект")
                continue
            miss = [k for k in OBS_EVENT_FIELDS if k not in event]
            if miss:
                warnings.append(f"events[{i}]: нет полей {miss}")
    if not isinstance(obs.get("constraints"), list):
        problems.append("observation.constraints должен быть массивом")
    if not isinstance(obs.get("metrics"), dict):
        problems.append("observation.metrics должен быть объектом")
    quality = obs.get("observation_quality")
    if not isinstance(quality, dict):
        problems.append("observation.observation_quality должен быть объектом")
    else:
        miss = [k for k in OBS_QUALITY_FIELDS if k not in quality]
        if miss:
            problems.append(f"observation_quality: нет полей {miss}")
    return problems, warnings


def _zone_boxes(obs):
    boxes = []
    zones = obs.get("zones") if isinstance(obs, dict) else None
    if not isinstance(zones, list):
        return boxes
    for zone in zones:
        if not isinstance(zone, dict):
            continue
        geom = zone.get("geometry")
        if not isinstance(geom, dict):
            continue
        mn = geom.get("min") if finite_vec3(geom.get("min")) else geom.get("lower")
        mx = geom.get("max") if finite_vec3(geom.get("max")) else geom.get("upper")
        if finite_vec3(mn) and finite_vec3(mx):
            boxes.append((zone.get("type"), mn, mx))
    return boxes


def _point_in_box(point, mn, mx, tol=1e-6):
    return all(mn[i] - tol <= point[i] <= mx[i] + tol for i in range(3))


def _point_in_any_zone(point, boxes):
    return any(_point_in_box(point, mn, mx) for _, mn, mx in boxes)


def looks_y_up(obs):
    boxes = _zone_boxes(obs)
    if boxes:
        pts = [p for _, mn, mx in boxes for p in (mn, mx)]
        sx = max(p[0] for p in pts) - min(p[0] for p in pts)
        sy = max(p[1] for p in pts) - min(p[1] for p in pts)
        sz = max(p[2] for p in pts) - min(p[2] for p in pts)
        if sx > 30.0 and sz > 30.0 and sy < min(sx, sz) * 0.55:
            return True
    ground = []
    for agent in obs.get("agents", []) if isinstance(obs.get("agents"), list) else []:
        if isinstance(agent, dict) and agent.get("kind") in ("ground", "surface_water") and finite_vec3(agent.get("position")):
            ground.append(agent["position"])
    if len(ground) >= 3:
        sx = max(p[0] for p in ground) - min(p[0] for p in ground)
        sy = max(p[1] for p in ground) - min(p[1] for p in ground)
        sz = max(p[2] for p in ground) - min(p[2] for p in ground)
        if sz > 30.0 and sy < sz * 0.25 and sx > 20.0:
            return True
    return False


def axis_command_problems(dec, obs, strict=False):
    problems = []
    warnings = []
    if looks_y_up(obs):
        return problems, ["наблюдение похоже на Y-up - проверка оси Z пропущена, среду нужно перевести на Z-up"]
    boxes = _zone_boxes(obs)
    if not boxes:
        return problems, []
    kinds = {}
    positions = {}
    for agent in obs.get("agents", []) if isinstance(obs.get("agents"), list) else []:
        if isinstance(agent, dict) and isinstance(agent.get("id"), str):
            kinds[agent["id"]] = agent.get("kind")
            if finite_vec3(agent.get("position")):
                positions[agent["id"]] = agent["position"]
    for cmd in dec.get("commands", []) if isinstance(dec.get("commands"), list) else []:
        if not isinstance(cmd, dict) or cmd.get("action") != "move":
            continue
        aid = cmd.get("agent_id")
        params = cmd.get("parameters") if isinstance(cmd.get("parameters"), dict) else {}
        target = params.get("target")
        if not finite_vec3(target):
            continue
        if not _point_in_any_zone(target, boxes):
            if strict:
                problems.append(f"move {aid}: target={target} вне всех зон мира (проверьте оси координат)")
            continue
        kind = kinds.get(aid)
        in_land = any(ztype in ("land", "water", "underground") and _point_in_box(target, mn, mx)
                      for ztype, mn, mx in boxes)
        # Если наземный агент резко меняет z относительно своей текущей высоты вне наземной зоны
        cur_p = positions.get(aid)
        if kind in ("ground", "surface_water") and not in_land and cur_p and abs(target[2] - cur_p[2]) > 5.0:
            msg = f"move {aid}: наземный агент {kind} получил target={target} с резким скачком высоты от z={cur_p[2]}"
            (problems if strict else warnings).append(msg)
    return problems, warnings


def has_stop_directive(obs):
    mission = obs.get("mission") if isinstance(obs, dict) else None
    directives = mission.get("directives") if isinstance(mission, dict) else []
    stop_words = {"safe_stop", "stop", "pause", "cancel", "abort", "halt"}
    for d in directives if isinstance(directives, list) else []:
        if not isinstance(d, dict):
            continue
        for key in ("type", "action", "command"):
            if str(d.get(key) or "").lower() in stop_words:
                return True
    events = obs.get("events") if isinstance(obs, dict) else None
    for e in events if isinstance(events, list) else []:
        if not isinstance(e, dict) or e.get("type") != "operator_directive":
            continue
        params = e.get("parameters") if isinstance(e.get("parameters"), dict) else {}
        for key in ("type", "action", "command"):
            if str(params.get(key) or "").lower() in stop_words:
                return True
    return False


def command_result_counts(results):
    accepted = rejected = unknown = 0
    for item in results if isinstance(results, list) else []:
        if not isinstance(item, dict):
            unknown += 1
            continue
        status = str(item.get("status") or item.get("validation_status") or "").lower()
        if status in ("accepted", "executed", "ok", "success", "completed", "applied", "done"):
            accepted += 1
        elif status in ("rejected", "failed", "error", "blocked", "invalid", "constraint_violation"):
            rejected += 1
        else:
            unknown += 1
    return accepted, rejected, unknown


def first_diff(a, b, path="$"):
    if type(a) is not type(b):
        return path, a, b
    if isinstance(a, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a or key not in b:
                return f"{path}.{key}", a.get(key, "<missing>"), b.get(key, "<missing>")
            diff = first_diff(a[key], b[key], f"{path}.{key}")
            if diff:
                return diff
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{path}.length", len(a), len(b)
        for i, (x, y) in enumerate(zip(a, b)):
            diff = first_diff(x, y, f"{path}[{i}]")
            if diff:
                return diff
        return None
    if a != b:
        return path, a, b
    return None


def stable_view(obs):
    out = copy.deepcopy(obs) if isinstance(obs, dict) else obs
    if isinstance(out, dict):
        out.pop("episode_id", None)
        out.pop("request_id", None)
        out.pop("extensions", None)
    return out


def extract_declared_scenarios(caps):
    declared = []
    if not isinstance(caps, dict):
        return declared
    for key in ("scenarios", "supported_scenarios"):
        val = caps.get(key)
        if isinstance(val, list):
            for s in val:
                sid = s if isinstance(s, str) else (s.get("id") if isinstance(s, dict) else None)
                if isinstance(sid, str) and sid not in declared:
                    declared.append(sid)
        elif isinstance(val, dict):
            for sid in val.keys():
                if isinstance(sid, str) and sid not in declared:
                    declared.append(sid)
    return declared


def check_environment(base_url, timeout=10.0, strict=False):
    rep = Report(f"Проверка среды моделирования: {base_url}", strict=strict)

    # 1. Health
    code, body, raw, dt = http_json(base_url, "GET", "/v1/environment/health", timeout=timeout)
    if code == 200 and isinstance(body, dict) and body.get("status") == "ready" and body.get("protocol_version") == PROTOCOL_VERSION:
        rep.ok("1. GET /v1/environment/health", f"environment_id={body.get('environment_id')}, {dt*1000:.0f} мс")
    else:
        rep.fail(
            "1. GET /v1/environment/health",
            f"HTTP {code}, body={raw[:200]}",
            fix="Поднимите среду и верните JSON {protocol_version:'1.0', component:'environment', "
                "status:'ready', environment_id, capabilities_url} (Listing 7).",
        )
        return rep

    # 2. Capabilities
    cap_url = body.get("capabilities_url") or "/v1/environment/capabilities"
    code, caps, raw, _ = http_json(base_url, "GET", cap_url, timeout=timeout)
    if code == 200 and isinstance(caps, dict):
        rep.ok("2. GET /v1/environment/capabilities", f"полей={len(caps)}")
    else:
        rep.fail(
            "2. GET /v1/environment/capabilities",
            f"HTTP {code}, body={raw[:200]}",
            fix="Реализуйте GET /v1/environment/capabilities со списком типов агентов, действий, зон, сценариев и операций.",
        )
        caps = {}

    supported_ops = None
    for key in ("supported_operations", "meta_operations", "operations"):
        if isinstance(caps.get(key), list):
            supported_ops = caps[key]
            break
    if supported_ops is None:
        rep.warn(
            "2b. capabilities.supported_operations",
            "список поддерживаемых операций не найден",
            fix="Перечислите в capabilities все 7 операций: keep, select, switch, modify, compose, synthesize, safe_stop.",
        )
    else:
        missing_ops = [op for op in META_OPERATIONS if op not in supported_ops]
        if missing_ops:
            rep.fail(
                "2b. capabilities.supported_operations",
                f"не хватает операций: {missing_ops}",
                fix="Среда обязана принимать все 7 операций метауровня.",
            )
        else:
            rep.ok("2b. capabilities.supported_operations", "все 7 операций заявлены")

    declared_sc = extract_declared_scenarios(caps)
    sc1 = declared_sc[0] if declared_sc else FALLBACK_SCENARIOS[0]
    sc2 = next((s for s in declared_sc[1:] if s != sc1), None)
    if not sc2:
        sc2 = next(s for s in FALLBACK_SCENARIOS if s != sc1)

    # 3. Reset
    reset_req = {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": f"chk_{RUN_TAG}_reset_001",
        "seed": 123456,
        "mission": {"id": "search_and_rescue", "parameters": {}},
    }
    code, reset_body, raw, dt = http_json(base_url, "POST", "/v1/environment/reset", reset_req, timeout=timeout)
    if code != 200 or not isinstance(reset_body, dict) or not reset_body.get("episode_id"):
        rep.fail(
            "3. POST /v1/environment/reset",
            f"HTTP {code}, body={raw[:250]}",
            fix="Верните 200 и {protocol_version, request_id, episode_id, time, status, observation} (Listing 10).",
        )
        return rep
    ep_id = reset_body["episode_id"]
    obs = unwrap_obs(reset_body)
    rep.ok("3. POST /v1/environment/reset", f"episode_id={ep_id}, size={len(raw)} байт, {dt*1000:.0f} мс")

    # 4a. Схема наблюдения
    obs_problems, obs_warnings = validate_observation(obs)
    if obs_problems:
        rep.fail(
            "4a. Схема observation (Табл. 9 + вложенные поля)",
            "; ".join(obs_problems[:6]) + (" ..." if len(obs_problems) > 6 else ""),
            fix="Приведите observation к Табл. 9 и вложенным Табл. 3/4/5/6/Listing 6.",
        )
    else:
        agents_count = len(obs.get("agents", [])) if isinstance(obs.get("agents"), list) else "?"
        zones_count = len(obs.get("zones", [])) if isinstance(obs.get("zones"), list) else "?"
        rep.ok("4a. Схема observation (Табл. 9 + вложенные поля)", f"агентов={agents_count}, зон={zones_count}")
    for warning in obs_warnings[:3]:
        rep.warn("4a. Схема observation: предупреждения", warning,
                 fix="Проверьте поля событий: id, type, time, source, targets, parameters, confidence, status.")

    # 4b. Ось высоты Z
    axis_reasons = []
    coords = caps.get("coordinates") if isinstance(caps.get("coordinates"), dict) else {}
    axes = coords.get("axes")
    if isinstance(axes, list) and len(axes) == 3:
        low = [str(a).lower() for a in axes]
        up_like = {"up", "height", "z", "z_up", "vertical"}
        if low[2] not in up_like or low[1] in {"up", "height", "z_up", "vertical"}:
            axis_reasons.append(f"capabilities.coordinates.axes={axes} (высота не третья координата)")
    vertical_axis = coords.get("vertical_axis") or coords.get("up_axis")
    if isinstance(vertical_axis, str) and vertical_axis.lower() not in ("z", "up", "height"):
        axis_reasons.append(f"capabilities.coordinates.vertical_axis={vertical_axis!r} (нужна ось Z)")

    boxes = _zone_boxes(obs)
    if boxes:
        pts = [p for _, mn, mx in boxes for p in (mn, mx)]
        sx = max(p[0] for p in pts) - min(p[0] for p in pts)
        sy = max(p[1] for p in pts) - min(p[1] for p in pts)
        sz = max(p[2] for p in pts) - min(p[2] for p in pts)
        if sx > 30.0 and sz > 30.0 and sy < min(sx, sz) * 0.55:
            axis_reasons.append(f"зоны мира имеют размах x={sx:.1f}, y={sy:.1f}, z={sz:.1f} (плоскость XZ, высота в Y)")
    if not axis_reasons:
        ground_like = []
        for a in obs.get("agents", []) if isinstance(obs.get("agents"), list) else []:
            if isinstance(a, dict) and a.get("kind") in ("ground", "surface_water") and finite_vec3(a.get("position")):
                ground_like.append(a["position"])
        for inf in obs.get("infrastructure", []) if isinstance(obs.get("infrastructure"), list) else []:
            if isinstance(inf, dict) and inf.get("kind") in ("base_station", "charging_station", "sensor_node") and finite_vec3(inf.get("position")):
                if max(abs(x) for x in inf["position"]) < 20000.0:
                    ground_like.append(inf["position"])
        for tgt in obs.get("targets", []) if isinstance(obs.get("targets"), list) else []:
            if isinstance(tgt, dict) and finite_vec3(tgt.get("position")):
                ground_like.append(tgt["position"])
        if len(ground_like) >= 3:
            sx = max(p[0] for p in ground_like) - min(p[0] for p in ground_like)
            sy = max(p[1] for p in ground_like) - min(p[1] for p in ground_like)
            sz = max(p[2] for p in ground_like) - min(p[2] for p in ground_like)
            if sz > 30.0 and sy < sz * 0.25 and sx > 20.0:
                axis_reasons.append(f"наземные объекты лежат в плоскости XZ (размах x={sx:.1f}, y={sy:.1f}, z={sz:.1f})")

    if axis_reasons:
        rep.fail(
            "4b. Ось высоты Z (3-я координата)",
            "; ".join(axis_reasons) + " - требуется высота по оси Z",
            fix="Пересчитайте координаты на границе HTTP в [x, y, z] с высотой по Z и обновите capabilities.coordinates.axes.",
        )
    else:
        rep.ok("4b. Ось высоты Z (3-я координата)", "признаков подмены оси Z на Y не обнаружено")

    # 4c. Нормировка ресурсов
    bad_res = []

    def check_resources(owner, resources):
        if not isinstance(resources, dict):
            return
        for rk, rv in resources.items():
            if isinstance(rv, dict) and "value" in rv:
                value, minimum, maximum = rv.get("value"), rv.get("minimum"), rv.get("maximum")
                if not _is_num(value) or not (-1e-9 <= value <= 1.0 + 1e-9):
                    bad_res.append(f"{owner}.{rk}.value={value}")
                if minimum is not None and (not _is_num(minimum) or not (-1e-9 <= minimum <= 1.0 + 1e-9)):
                    bad_res.append(f"{owner}.{rk}.minimum={minimum}")
                if maximum is not None and (not _is_num(maximum) or not (-1e-9 <= maximum <= 1.0 + 1e-9)):
                    bad_res.append(f"{owner}.{rk}.maximum={maximum}")
                if _is_num(value) and _is_num(minimum) and _is_num(maximum) and not (minimum <= value <= maximum):
                    bad_res.append(f"{owner}.{rk}: нарушено minimum<=value<=maximum")
            elif _is_num(rv):
                if not (-1e-9 <= rv <= 1.0 + 1e-9):
                    bad_res.append(f"{owner}.{rk}={rv}")

    for a in obs.get("agents", []) if isinstance(obs.get("agents"), list) else []:
        if isinstance(a, dict):
            check_resources(a.get("id", "?"), a.get("resources"))
    for inf in obs.get("infrastructure", []) if isinstance(obs.get("infrastructure"), list) else []:
        if isinstance(inf, dict):
            check_resources(inf.get("id", "?"), inf.get("resources"))
    if bad_res:
        rep.fail(
            "4c. Нормировка ресурсов [0, 1]",
            "вне диапазона/порядка: " + ", ".join(bad_res[:6]),
            fix="Для каждого нормированного ресурса обеспечьте 0 <= minimum <= value <= maximum <= 1 и unit='normalized'.",
        )
    else:
        rep.ok("4c. Нормировка ресурсов [0, 1]", "все нормированные ресурсы корректны")

    # 4d. Поля метрик
    m = obs.get("metrics") if isinstance(obs.get("metrics"), dict) else {}
    has_prog = any(k in m for k in ("mission_completion", "M1", "m1", "completion", "targets_rescued", "targets_total"))
    has_viol = any(k in m for k in ("safety_violations", "violations", "collisions", "contacts"))
    if has_prog and has_viol:
        rep.ok("4d. Поля metrics (прогресс и безопасность)", f"ключи={sorted(m.keys())[:10]}")
    else:
        missing = ([k for k, ok_f in (("mission_completion", has_prog), ("safety_violations", has_viol)) if not ok_f])
        rep.warn(
            "4d. Поля metrics (прогресс и безопасность)",
            f"нет {missing}, сейчас ключи={sorted(m.keys())[:10]}",
            fix="Добавьте в observation.metrics поля mission_completion (0..1) и safety_violations (целое >= 0).",
        )

    # 5. GET observation
    code, obs2_body, raw, _ = http_json(base_url, "GET", "/v1/environment/observation", timeout=timeout)
    if code == 200 and isinstance(obs2_body, dict):
        obs2 = unwrap_obs(obs2_body)
        problems2, _ = validate_observation(obs2)
        if problems2:
            rep.fail("5. GET /v1/environment/observation", "неполное observation: " + "; ".join(problems2[:5]),
                     fix="GET /v1/environment/observation должен возвращать полное наблюдение Табл. 9.")
        else:
            rep.ok("5. GET /v1/environment/observation", f"time={obs2.get('time')}")
    else:
        rep.fail("5. GET /v1/environment/observation", f"HTTP {code}, body={raw[:200]}",
                 fix="Реализуйте GET /v1/environment/observation.")

    # 6. Серия шагов
    cur_obs = obs
    cur_time = cur_obs.get("time", 0.0)
    agents = cur_obs.get("agents") if isinstance(cur_obs.get("agents"), list) else []
    active_agents = [a for a in agents if isinstance(a, dict) and a.get("status") == "active"]
    first_ag = active_agents[0] if active_agents else (agents[0] if agents else None)
    roles1 = {first_ag["id"]: "scout"} if isinstance(first_ag, dict) and first_ag.get("id") else {}
    cur_scenarios = []
    cur_params = {}
    step_counter = [0]

    def refresh_obs():
        nonlocal cur_obs, cur_time
        c, b, _, _ = http_json(base_url, "GET", "/v1/environment/observation", timeout=timeout)
        if c == 200 and isinstance(b, dict):
            cur_obs = unwrap_obs(b)
            val = cur_obs.get("time", cur_time)
            if _is_num(val):
                cur_time = val
            return True
        return False

    def run_step(label, op, sc_ids, params=None, cmds=None, synth=None,
                 check_optional_ignore=False, require_time=True, fix=""):
        nonlocal cur_obs, cur_time, cur_scenarios, cur_params
        step_counter[0] += 1
        dec = make_clean_decision(
            ep_id, 100 + step_counter[0], cur_time, op, sc_ids, cur_scenarios,
            roles1, roles1 if cur_scenarios else {}, commands=cmds, params=params, synth=synth,
        )
        if check_optional_ignore:
            dec["_tournament_optional_meta"] = {"source": "checker"}
        c, sb, r, _ = http_json(base_url, "POST", "/v1/environment/step", dec, timeout=timeout)
        if c == 200 and isinstance(sb, dict):
            new_obs = unwrap_obs(sb)
            new_time = sb.get("time", new_obs.get("time", cur_time))
            if not _is_num(new_time):
                rep.fail(label, "в ответе нет числового time", fix="Верните time в ответе step (Listing 11).")
                return False, sb
            if require_time and _is_num(cur_time) and new_time <= cur_time + 1e-9:
                rep.fail(label, f"time не продвинулось: {cur_time} -> {new_time}",
                         fix="Шаг среды обязан продвигать модельное время (п. 1.13.5).")
                return False, sb
            problems, _ = validate_observation(new_obs)
            if problems:
                rep.fail(label, "ответ step содержит неполное observation: " + "; ".join(problems[:5]),
                         fix="Верните полное observation Табл. 9 в ответе step.")
                return False, sb
            cur_obs, cur_time = new_obs, new_time
            cur_scenarios, cur_params = list(sc_ids), dict(params or {})
            rep.ok(label, f"time={cur_time}")
            return True, sb
        rep.fail(label, f"HTTP {c}: {r[:260]}", fix=fix)
        return False, None

    ok, _ = run_step("6a. POST /step (select)", "select", [sc1], check_optional_ignore=True,
                     fix="Среда должна принимать select и игнорировать неизвестные необязательные поля решения (п. 1.13.1).")
    if not ok:
        return rep
    run_step("6b. POST /step (keep)", "keep", cur_scenarios, params=cur_params,
             fix="Среда должна принимать keep с текущей конфигурацией.")
    run_step("6c. POST /step (modify)", "modify", cur_scenarios, params={"priority_bias": 1.2},
             fix="Среда должна принимать modify без требования авторских полей в parameters.")
    run_step("6d. POST /step (compose)", "compose", [sc1, sc2], params={},
             fix="Среда должна принимать compose двух сценариев.")

    # 6e. switch + move + игнорирование необязательного поля в commands[].parameters
    # На каждой попытке используем новый step_num (уникальный request_id/decision_id),
    # чтобы не попадать в кэш идемпотентности (409 Conflict).
    candidates = [
        a for a in (cur_obs.get("agents", []) if isinstance(cur_obs.get("agents"), list) else [])
        if isinstance(a, dict) and a.get("status") == "active" and finite_vec3(a.get("position"))
        and "move" in {action_name(x) for x in a.get("available_actions", [])}
    ]
    if not candidates:
        rep.warn("6e. POST /step (switch + move + ignore optional)",
                 "нет активного агента с действием move - проверить п. 1.13.1 не удалось",
                 fix="Добавьте активного агента с move в available_actions.")
    else:
        last_error = ""
        done_6e = False
        attempt = 0
        for agent in candidates[:3]:
            p0 = list(agent["position"])
            targets = [p0, [p0[0] + 1.0, p0[1], p0[2]], [p0[0] - 1.0, p0[1], p0[2]], [p0[0], p0[1] + 1.0, p0[2]]]
            for target in targets:
                attempt += 1
                cmd = [{"agent_id": agent["id"], "action": "move",
                        "parameters": {"target": target, "_optional_hint": "ignore_me"}}]
                dec = make_clean_decision(
                    ep_id, 200 + attempt, cur_time, "switch", [sc2], cur_scenarios,
                    roles1, roles1, commands=cmd, params={},
                )
                c, sb, r, _ = http_json(base_url, "POST", "/v1/environment/step", dec, timeout=timeout)
                if c == 200 and isinstance(sb, dict):
                    new_obs = unwrap_obs(sb)
                    new_time = sb.get("time", new_obs.get("time", cur_time))
                    problems, _ = validate_observation(new_obs)
                    if problems:
                        rep.fail("6e. POST /step (switch + move + ignore optional)",
                                 "ответ step неполный: " + "; ".join(problems[:5]),
                                 fix="Верните полное observation Табл. 9.")
                    elif not _is_num(new_time) or new_time <= cur_time + 1e-9:
                        rep.fail("6e. POST /step (switch + move + ignore optional)",
                                 f"time не продвинулось: {cur_time} -> {new_time}",
                                 fix="Шаг с move должен продвигать модельное время.")
                    else:
                        cur_obs, cur_time = new_obs, new_time
                        cur_scenarios, cur_params = [sc2], {}
                        rep.ok("6e. POST /step (switch + move + ignore optional, п.1.13.1)",
                               f"агент={agent['id']}, target={target}, time={cur_time}")
                    done_6e = True
                    break
                last_error = f"HTTP {c}: {r[:220]}"
                text = r.lower()
                if ("_optional_hint" in r or "неизвест" in text or "unknown" in text
                        or "unexpected" in text or "extra field" in text or "additional" in text):
                    rep.fail(
                        "6e. POST /step (switch + move + ignore optional, п.1.13.1)",
                        f"среда отклонила неизвестное необязательное поле в commands[].parameters: {last_error}",
                        fix="Среда обязана игнорировать неизвестные необязательные поля в commands[].parameters (п. 1.13.1).",
                    )
                    done_6e = True
                    break
            if done_6e:
                break
        if not done_6e:
            rep.warn("6e. POST /step (switch + move + ignore optional, п.1.13.1)",
                     f"не удалось подобрать свободную точку move, последняя ошибка: {last_error}",
                     fix="Проверьте вручную: отправьте move с безопасной точкой и лишним полем в parameters.")
            refresh_obs()

    # 6f. synthesize по Listing 3
    run_step("6f. POST /step (synthesize, базовый профиль)",
             "synthesize", [MIN_SYNTH_SCENARIO["id"]], synth=copy.deepcopy(MIN_SYNTH_SCENARIO),
             fix="Среда должна принимать synthesize по Listing 3 без требования недокументированных полей вроде graph.")

    # 7. Некорректная команда
    bad_dec = make_clean_decision(
        ep_id, 900, cur_time, "keep", cur_scenarios, cur_scenarios, roles1, roles1,
        params=cur_params,
        commands=[{"agent_id": "__nonexistent_agent__", "action": "__bad_action__", "parameters": {}}],
    )
    code, err_body, raw, _ = http_json(base_url, "POST", "/v1/environment/step", bad_dec, timeout=timeout)
    if 400 <= code < 500:
        err_code = (err_body.get("error") or {}).get("code") if isinstance(err_body, dict) else None
        if err_code in ERROR_CODES:
            rep.ok("7. Ошибка на некорректную команду", f"HTTP {code}, code={err_code}")
        else:
            rep.warn("7. Ошибка на некорректную команду", f"HTTP {code}, нестандартный code={err_code}",
                     fix="Возвращайте нормативный error.code из п. 1.13.6 (INVALID_COMMAND и др.).")
    elif code == 200 and isinstance(err_body, dict):
        crs = err_body.get("command_results", [])
        if any(isinstance(c, dict) and str(c.get("status") or c.get("validation_status") or "").lower()
               in ("rejected", "failed", "error", "blocked") for c in crs):
            cur_obs = unwrap_obs(err_body)
            val = err_body.get("time", cur_obs.get("time", cur_time))
            if _is_num(val):
                cur_time = val
            rep.ok("7. Ошибка на некорректную команду", "отклонена через command_results")
        else:
            rep.fail("7. Ошибка на некорректную команду", "среда приняла несуществующего агента без ошибки",
                     fix="Неизвестный агент должен давать 4xx с error.code или command_results.status='rejected'.")
    else:
        rep.fail("7. Ошибка на некорректную команду", f"HTTP {code}, body={raw[:220]}",
                 fix="Некорректная команда должна давать 4xx по Listing 12.")

    # 8. safe_stop
    ok8, sb8 = run_step("8. POST /step (safe_stop)", "safe_stop", cur_scenarios or [sc1], params=cur_params,
                        require_time=False, fix="Среда должна принимать safe_stop.")
    if ok8 and isinstance(sb8, dict):
        status = str(sb8.get("status", "")).lower()
        done = bool(sb8.get("done"))
        if status in ("safe_stopped", "success", "failed", "time_limit", "completed") or done:
            rep.ok("8b. Статус после safe_stop", f"status={status or '-'}, done={done}")
        else:
            rep.warn("8b. Статус после safe_stop",
                     f"status={status or '-'}, done={done} (среда приняла safe_stop, но эпизод остался running)",
                     fix="После safe_stop переведите эпизод в status='safe_stopped' (или done=true).")

    # 9. Журнал
    code, _, raw, _ = http_json(base_url, "GET", "/v1/environment/logs", timeout=timeout)
    if code == 200 and raw.strip():
        parsed = False
        try:
            json.loads(raw)
            parsed = True
        except Exception:
            try:
                lines = [line for line in raw.splitlines() if line.strip()]
                if lines:
                    for line in lines:
                        json.loads(line)
                    parsed = True
            except Exception:
                parsed = False
        if parsed:
            rep.ok("9. GET /v1/environment/logs", f"машиночитаемый JSON/JSONL, размер={len(raw)} байт")
        else:
            rep.fail("9. GET /v1/environment/logs", f"ответ не парсится как JSON/JSONL, размер={len(raw)} байт",
                     fix="Журнал должен быть в JSON Lines или валидном JSON (п. 1.14).")
    else:
        rep.fail("9. GET /v1/environment/logs", f"HTTP {code}, body={raw[:220]}",
                 fix="Реализуйте GET /v1/environment/logs.")

    # 10. close
    code, _, raw, _ = http_json(
        base_url, "POST", "/v1/environment/close",
        {"protocol_version": PROTOCOL_VERSION, "episode_id": ep_id, "request_id": f"chk_{RUN_TAG}_close_1"},
        timeout=timeout,
    )
    if code == 200:
        rep.ok("10. POST /v1/environment/close", "эпизод корректно закрыт")
    else:
        rep.fail("10. POST /v1/environment/close", f"HTTP {code}, body={raw[:200]}",
                 fix="Закрытие эпизода обязательно (Табл. 10) - верните 200 и освободите ресурсы.")

    # 11. Детерминизм по seed
    sigs = []
    det_errors = []
    for run_i in (1, 2):
        r_req = {"protocol_version": PROTOCOL_VERSION, "request_id": f"chk_{RUN_TAG}_det_r_{run_i}",
                 "seed": 777, "mission": {"id": "search_and_rescue", "parameters": {}}}
        c1, b1, _, _ = http_json(base_url, "POST", "/v1/environment/reset", r_req, timeout=timeout)
        if c1 != 200 or not isinstance(b1, dict):
            det_errors.append(f"reset #{run_i}: HTTP {c1}")
            break
        o1 = unwrap_obs(b1)
        d_req = make_clean_decision(b1["episode_id"], 110 + run_i, o1.get("time", 0.0), "select", [sc1])
        c2, b2, _, _ = http_json(base_url, "POST", "/v1/environment/step", d_req, timeout=timeout)
        if c2 != 200 or not isinstance(b2, dict):
            det_errors.append(f"step #{run_i}: HTTP {c2}")
            break
        sigs.append(stable_view(unwrap_obs(b2)))
        http_json(base_url, "POST", "/v1/environment/close",
                  {"protocol_version": PROTOCOL_VERSION, "episode_id": b1["episode_id"],
                   "request_id": f"chk_{RUN_TAG}_det_c_{run_i}"}, timeout=timeout)
    if len(sigs) == 2:
        diff = first_diff(sigs[0], sigs[1])
        if diff is None:
            rep.ok("11. Детерминизм по seed=777", "полные публичные наблюдения совпали")
        else:
            path, a, b = diff
            rep.fail("11. Детерминизм по seed=777", f"расхождение {path}: {a!r} != {b!r}",
                     fix="Обеспечьте воспроизводимость при одинаковом seed и одинаковых командах (п. 1.15).")
    else:
        rep.fail("11. Детерминизм по seed=777", "; ".join(det_errors) or "не удалось выполнить два прогона",
                 fix="Проверьте reset/step с seed=777.")

    return rep


def make_synthetic_obs(step_num, dialect="object_action", episode_id=None, disturbed=False,
                       safe_stop=False, minimal=False, extra=False):
    def fmt_acts(names):
        if dialect == "strings":
            return list(names)
        if dialect == "object_id":
            return [{"id": n} for n in names]
        return [{"action": n, "parameters_schema": {"type": "object"}} for n in names]

    energy_a1 = 0.18 if disturbed else 0.85
    conn_a2 = not disturbed
    events = []
    directives = []
    if disturbed:
        events.append({
            "id": f"ev_conn_{step_num}", "type": "connectivity_loss", "time": float(step_num * 2),
            "source": "network", "targets": ["ent_ag_002"], "parameters": {}, "confidence": 0.9, "status": "active",
        })
        events.append({
            "id": f"ev_low_{step_num}", "type": "resource_shortage", "time": float(step_num * 2),
            "source": "ent_ag_001", "targets": ["ent_ag_001"], "parameters": {"resource": "energy"},
            "confidence": 1.0, "status": "active",
        })
    if safe_stop:
        d = {"id": "dir_stop_1", "type": "safe_stop", "action": "safe_stop", "command": "stop", "priority": "critical"}
        directives.append(d)
        events.append({
            "id": f"ev_dir_{step_num}", "type": "operator_directive", "time": float(step_num * 2),
            "source": "operator", "targets": [], "parameters": d, "confidence": 1.0, "status": "new",
        })

    # Ресурсы строго по Listing 1 Приложения А (включая поле "id": "energy").
    # Зоны land и air стыкуются без мёртвой щели по z (land z in [-0.5, 2.0], air z in [0.0, 100.0]).
    obs = {
        "protocol_version": PROTOCOL_VERSION,
        "episode_id": episode_id or f"ep_synth_{dialect}_{step_num}",
        "request_id": f"req_synth_{step_num}",
        "time": float(step_num * 2),
        "mission": {
            "id": "search_and_rescue",
            "objectives": [{"id": "obj_1", "type": "rescue", "priority": 1.0, "completion": 0.0, "status": "active"}],
            "priorities": ["safety", "rescue", "confirm", "search", "connectivity"],
            "directives": directives,
            "constraints": [{"id": "minimum_energy_reserve", "minimum": 0.15}],
            "status": "active",
            "termination": {"time_limit": 300.0},
        },
        "agents": [
            {
                "id": "ent_ag_001", "kind": "aerial", "position": [10.0, 15.0, 25.0], "orientation": [0.0, 0.0, 0.0],
                "status": "active", "capabilities": ["move", "sense", "relay", "charge"],
                "resources": {"energy": {"id": "energy", "value": energy_a1, "minimum": 0.15, "maximum": 1.0, "unit": "normalized"}},
                "communication": {"connected": True, "quality": 0.9},
                "role": None, "scenario_ids": [],
                "available_actions": fmt_acts(["move", "observe", "relay", "charge"]),
                "constraints": [],
            },
            {
                "id": "ent_ag_002", "kind": "ground", "position": [40.0, 20.0, 0.0], "orientation": [0.0, 0.0, 0.0],
                "status": "active", "capabilities": ["move", "deliver", "rescue", "charge"],
                "resources": {"energy": {"id": "energy", "value": 0.72, "minimum": 0.15, "maximum": 1.0, "unit": "normalized"}},
                "communication": {"connected": conn_a2, "quality": 0.8 if conn_a2 else 0.0},
                "role": None, "scenario_ids": [],
                "available_actions": fmt_acts(["move", "deliver", "charge"]),
                "constraints": [],
            },
        ],
        "infrastructure": [
            {"id": "ent_inf_001", "kind": "base_station", "position": [0.0, 0.0, 0.0], "status": "active",
             "capabilities": ["communicate"], "resources": {}, "communication": {"connected": True, "quality": 1.0}, "constraints": []},
            {"id": "ent_inf_002", "kind": "charging_station", "position": [5.0, 5.0, 0.0], "status": "active",
             "capabilities": ["charge"], "resources": {}, "communication": {"connected": True, "quality": 1.0}, "constraints": []},
            {"id": "ent_inf_003", "kind": "relay", "position": [30.0, 10.0, 5.0], "status": "active",
             "capabilities": ["relay"], "resources": {}, "communication": {"connected": True, "quality": 0.9}, "constraints": []},
            {"id": "ent_inf_004", "kind": "sensor_node", "position": [50.0, 50.0, 2.0], "status": "active",
             "capabilities": ["sense"], "resources": {}, "communication": {"connected": True, "quality": 0.8}, "constraints": []},
        ],
        "targets": [
            {"id": "ent_tgt_001", "type": "person", "position": [60.0, 45.0, 0.0], "priority": 1.0,
             "status": "detected", "confidence": 0.85},
        ],
        "zones": [
            {"id": "ent_zn_001", "type": "land",
             "geometry": {"type": "axis_aligned_box", "min": [0.0, 0.0, -0.5], "max": [200.0, 200.0, 2.0]},
             "access": {}, "risk": 0.0, "cost": {}, "communication": {}, "constraints": []},
            {"id": "ent_zn_002", "type": "restricted",
             "geometry": {"type": "axis_aligned_box", "min": [80.0, 80.0, 0.0], "max": [100.0, 100.0, 50.0]},
             "access": {}, "risk": 1.0, "cost": {}, "communication": {}, "constraints": []},
            {"id": "ent_zn_air_001", "type": "air",
             "geometry": {"type": "axis_aligned_box", "min": [0.0, 0.0, 0.0], "max": [200.0, 200.0, 100.0]},
             "access": {}, "risk": 0.0, "cost": {}, "communication": {}, "constraints": []},
        ],
        "network": {
            "connectivity_score": 0.5 if disturbed else 0.95,
            "partition_count": 2 if disturbed else 1,
            "reachable_agent_ratio": 0.5 if disturbed else 1.0,
            "average_link_quality": 0.6 if disturbed else 0.9,
            "mesh_available": True,
            "known_links": [],
        },
        "events": events,
        "constraints": [{"id": "minimum_energy_reserve", "minimum": 0.15}],
        "metrics": {"mission_completion": 0.0, "safety_violations": 0},
        "observation_quality": {
            "global_completeness": 0.7 if disturbed else 0.95,
            "maximum_delay": 2.0 if disturbed else 0.1,
            "stale_data_ratio": 0.25 if disturbed else 0.0,
            "contradiction_count": 1 if disturbed else 0,
            "unknown_state_ratio": 0.2 if disturbed else 0.05,
        },
    }

    if minimal:
        for agent in obs["agents"]:
            agent.pop("orientation", None)
            agent.pop("trust", None)

    if extra:
        obs["_author_specific"] = {"team": "checker-test"}
        obs["mission"]["_author_specific"] = "must-be-ignored"
        obs["agents"][0]["_author_specific"] = {"source": "team-extension"}
        obs["network"]["_author_specific"] = [1, 2, 3]

    return obs


def make_controller_suite():
    suite = []
    base = make_synthetic_obs(0, "object_action", "ep_suite_base")
    suite.append(("base", base))

    conn = copy.deepcopy(base)
    conn["time"] = 2.0
    conn["request_id"] = "req_flow_conn"
    conn["network"].update({"connectivity_score": 0.2, "partition_count": 2,
                            "reachable_agent_ratio": 0.4, "average_link_quality": 0.3})
    conn["events"] = [{
        "id": "ev_conn", "type": "connectivity_loss", "time": 2.0, "source": "network",
        "targets": ["ent_ag_002"], "parameters": {}, "confidence": 0.9, "status": "active",
    }]
    conn["mission"]["priorities"] = ["connectivity", "search", "safety"]
    suite.append(("flow_conn", conn))

    low = copy.deepcopy(base)
    low["time"] = 4.0
    low["request_id"] = "req_flow_low"
    low["agents"][0]["resources"]["energy"]["value"] = 0.12
    low["events"] = [{
        "id": "ev_low", "type": "resource_shortage", "time": 4.0, "source": "ent_ag_001",
        "targets": ["ent_ag_001"], "parameters": {"resource": "energy"},
        "confidence": 1.0, "status": "active",
    }]
    suite.append(("flow_low", low))

    multi = copy.deepcopy(base)
    multi["time"] = 6.0
    multi["request_id"] = "req_flow_multi"
    multi["agents"][0]["scenario_ids"] = ["coverage_search", "relay_coalition"]
    multi["agents"][1]["scenario_ids"] = ["coverage_search"]
    suite.append(("flow_multi", multi))

    inspect = copy.deepcopy(base)
    inspect["time"] = 8.0
    inspect["request_id"] = "req_flow_inspect"
    inspect["mission"]["directives"] = [{
        "id": "dir_inspect", "type": "inspect_point", "action": "inspect_point",
        "point": [85.0, 85.0], "agent_kind": "aerial", "priority": "high",
    }]
    suite.append(("flow_inspect", inspect))

    stop = copy.deepcopy(base)
    stop["time"] = 10.0
    stop["request_id"] = "req_flow_stop"
    directive = {"id": "dir_stop_1", "type": "safe_stop", "action": "safe_stop",
                 "command": "stop", "priority": "critical"}
    stop["mission"]["directives"] = [directive]
    stop["events"] = [{
        "id": "ev_dir_1", "type": "operator_directive", "time": 10.0,
        "source": "operator", "targets": [], "parameters": directive,
        "confidence": 1.0, "status": "new",
    }]
    suite.append(("flow_stop", stop))

    suite.append(("dialect_strings", make_synthetic_obs(0, "strings", "ep_suite_strings")))
    suite.append(("dialect_object_id", make_synthetic_obs(0, "object_id", "ep_suite_object_id")))
    suite.append(("minimal", make_synthetic_obs(0, "object_action", "ep_suite_minimal", minimal=True)))
    extra_base = make_synthetic_obs(0, "object_action", "ep_suite_extra")
    extra = make_synthetic_obs(0, "object_action", "ep_suite_extra", extra=True)
    suite.append(("undocumented_base", extra_base))
    suite.append(("undocumented_extra", extra))
    suite.append(("disturbed", make_synthetic_obs(4, "object_action", "ep_suite_disturbed", disturbed=True)))

    priority = copy.deepcopy(extra_base)
    priority["episode_id"] = "ep_suite_priority"
    priority["targets"][0]["priority"] = 1.0
    priority["targets"][0]["confidence"] = 1.0
    priority["events"] = [{
        "id": "ev_target", "type": "target_detected", "time": 0.0, "source": "sensor",
        "targets": ["ent_tgt_001"], "parameters": {"priority": 1.0}, "confidence": 0.95, "status": "new",
    }]
    suite.append(("priority_target", priority))
    return suite


def validate_decision_struct(dec, obs, full=True, check_axes=True, strict_axis=False):
    problems = []
    warnings = []
    if not isinstance(dec, dict):
        return ["ответ не является JSON-объектом"], warnings

    meta = dec.get("meta_decision") if isinstance(dec.get("meta_decision"), dict) else {}
    op = dec.get("operation") or meta.get("operation")
    if op not in META_OPERATIONS:
        problems.append(f"недопустимое operation={op!r}")

    if full:
        for key in sorted(DECISION_FIELDS):
            if key not in dec:
                problems.append(f"нет обязательного поля {key}")
        if dec.get("protocol_version") != PROTOCOL_VERSION:
            problems.append(f"protocol_version={dec.get('protocol_version')!r}, ожидается '1.0'")
        for key in ("episode_id", "decision_id", "request_id"):
            if not isinstance(dec.get(key), str):
                problems.append(f"{key} должен быть строкой")
        if not _is_num(dec.get("time")):
            problems.append("time должен быть числом")

    sel = dec.get("selected_configuration")
    if not isinstance(sel, dict):
        if full and not meta:
            problems.append("нет selected_configuration")
        elif full:
            warnings.append("используется meta_decision вместо selected_configuration")
    else:
        if full:
            for key in ("scenario_ids", "parameters", "synthesized_scenario"):
                if key not in sel:
                    problems.append(f"selected_configuration: нет поля {key}")
        if "scenario_ids" in sel and not isinstance(sel["scenario_ids"], list):
            problems.append("selected_configuration.scenario_ids должен быть массивом")
        if "parameters" in sel and not isinstance(sel["parameters"], dict):
            problems.append("selected_configuration.parameters должен быть объектом")
        if sel.get("synthesized_scenario") is not None and not isinstance(sel["synthesized_scenario"], dict):
            problems.append("selected_configuration.synthesized_scenario должен быть объектом или null")

    prev = dec.get("previous_configuration")
    if full:
        if not isinstance(prev, dict):
            problems.append("previous_configuration должен быть объектом")
        else:
            if not isinstance(prev.get("scenario_ids"), list):
                problems.append("previous_configuration.scenario_ids должен быть массивом")
            if not isinstance(prev.get("role_assignments"), dict):
                problems.append("previous_configuration.role_assignments должен быть объектом")

    candidates = dec.get("candidate_evaluations")
    if full and not isinstance(candidates, list):
        problems.append("candidate_evaluations должен быть массивом")
    elif isinstance(candidates, list):
        for i, item in enumerate(candidates[:8]):
            if not isinstance(item, dict):
                warnings.append(f"candidate_evaluations[{i}] не объект")
                continue
            for key in ("scenario_id", "applicable", "utility", "risk"):
                if key not in item:
                    warnings.append(f"candidate_evaluations[{i}]: нет поля {key}")

    y_up_obs = looks_y_up(obs)
    valid_agents = {}
    agent_kinds = {}
    for agent in obs.get("agents", []) if isinstance(obs.get("agents"), list) else []:
        if isinstance(agent, dict) and isinstance(agent.get("id"), str):
            valid_agents[agent["id"]] = {action_name(x) for x in agent.get("available_actions", [])}
            agent_kinds[agent["id"]] = agent.get("kind")

    roles = dec.get("role_assignments")
    if full and not isinstance(roles, dict):
        problems.append("role_assignments должен быть объектом")
    elif isinstance(roles, dict):
        for agent_id in roles:
            if agent_id not in valid_agents:
                problems.append(f"role_assignments: неизвестный агент {agent_id}")

    commands = dec.get("commands")
    if not isinstance(commands, list):
        problems.append("нет массива commands")
        commands = []
    for i, cmd in enumerate(commands[:64]):
        if not isinstance(cmd, dict):
            problems.append(f"commands[{i}] не объект")
            continue
        aid, act = cmd.get("agent_id"), cmd.get("action")
        if aid not in valid_agents:
            problems.append(f"команда неизвестному агенту {aid}")
        elif valid_agents[aid] and act not in valid_agents[aid]:
            problems.append(f"недоступное действие {act} для {aid}")
        elif not valid_agents[aid]:
            warnings.append(f"commands[{i}]: у агента {aid} пустой available_actions")
        params = cmd.get("parameters")
        if params is not None and not isinstance(params, dict):
            problems.append(f"commands[{i}].parameters должен быть объектом")
        if act == "move":
            params = params if isinstance(params, dict) else {}
            target = params.get("target")
            if not finite_vec3(target):
                problems.append(f"команда move для {aid} должна содержать parameters.target=[x,y,z]")
            elif agent_kinds.get(aid) == "aerial" and target[2] < -1.0 and not y_up_obs:
                problems.append(f"aerial-агент {aid} направлен в отрицательный z={target[2]} (ошибка оси высоты Z?)")

    if full and check_axes:
        axis_problems, axis_warnings = axis_command_problems(dec, obs, strict=strict_axis)
        problems.extend(axis_problems)
        warnings.extend(axis_warnings)

    for cc in dec.get("constraint_checks", []) if isinstance(dec.get("constraint_checks"), list) else []:
        if isinstance(cc, dict) and cc.get("status") not in STANDARD_CHECK_STATUSES:
            warnings.append(f"нестандартный constraint_checks.status={cc.get('status')!r} (рекомендуется passed/failed/unknown)")
            break

    explanation = dec.get("explanation")
    if full and not isinstance(explanation, dict):
        problems.append("explanation должен быть объектом")
    elif isinstance(explanation, dict):
        for key in ("observation_refs", "reason_codes", "expected_effects", "confidence"):
            if key not in explanation:
                warnings.append(f"explanation: нет поля {key} (Listing 5)")

    return problems, warnings


def _decision_signature(dec):
    if not isinstance(dec, dict):
        return dec
    sel = dec.get("selected_configuration") if isinstance(dec.get("selected_configuration"), dict) else {}
    return {
        "operation": dec.get("operation") or (dec.get("meta_decision") or {}).get("operation"),
        "scenario_ids": sel.get("scenario_ids"),
        "parameters": sel.get("parameters"),
        "synthesized_scenario": sel.get("synthesized_scenario"),
        "role_assignments": dec.get("role_assignments"),
        "commands": dec.get("commands"),
    }


def check_controller(base_url, timeout=10.0, strict=False):
    rep = Report(f"Проверка метасценарного контроллера: {base_url}", strict=strict)

    # 1. Health
    code, body, raw, dt = http_json(base_url, "GET", "/v1/controller/health", timeout=timeout)
    if code == 200 and isinstance(body, dict) and body.get("status") == "ready" and body.get("protocol_version") == PROTOCOL_VERSION:
        ops = body.get("supported_operations")
        if isinstance(ops, list):
            missing = [op for op in META_OPERATIONS if op not in ops]
            if missing:
                rep.fail("1b. GET /v1/controller/health: supported_operations", f"не хватает операций: {missing}",
                         fix="Перечислите в supported_operations все 7 операций.")
            else:
                rep.ok("1b. GET /v1/controller/health: supported_operations", "все 7 операций заявлены")
        else:
            rep.warn("1b. GET /v1/controller/health: supported_operations", "поле отсутствует",
                     fix="Верните supported_operations со всеми 7 операциями (Listing 8).")
        rep.ok("1. GET /v1/controller/health", f"controller_id={body.get('controller_id')}, {dt*1000:.0f} мс")
    else:
        rep.fail("1. GET /v1/controller/health", f"HTTP {code}, body={raw[:220]}",
                 fix="Поднимите контроллер и верните JSON по Listing 8.")
        return rep

    suite = make_controller_suite()
    observed_ops = set()
    warning_notes = []

    def send_decide(observation):
        return http_json(base_url, "POST", "/v1/controller/decide", {"observation": observation}, timeout=timeout)

    def run_case(label, observation, fix, collect=True):
        c, dec, r, elapsed = send_decide(observation)
        if c != 200 or not isinstance(dec, dict):
            rep.fail(label, f"HTTP {c}: {r[:260]}", fix=fix)
            return None
        problems, warnings = validate_decision_struct(dec, observation, strict_axis=True)
        warning_notes.extend(warnings)
        op = dec.get("operation") or (dec.get("meta_decision") or {}).get("operation")
        if collect and op in META_OPERATIONS:
            observed_ops.add(op)
        if problems:
            axis_hint = ("Проверьте оси [x, y, z] с высотой z. " if any("зон" in p or "оси" in p for p in problems) else "")
            rep.fail(label, "; ".join(problems[:6]), fix=axis_hint + fix)
            return dec
        if elapsed > 5.0:
            rep.warn(label, f"медленный ответ {elapsed:.2f} с > 5 с",
                     fix="Желательное время ответа до 1 с, жёсткий таймаут 15 с.")
        else:
            rep.ok(label, f"op={op}, команд={len(dec.get('commands', []))}, {elapsed*1000:.0f} мс")
        return dec

    labels = {
        "base": "2. Базовое наблюдение (Listing 5)",
        "dialect_strings": "3. Диалект available_actions=['move',...]",
        "dialect_object_id": "4. Диалект available_actions=[{id:...}]",
        "minimal": "5. Отсутствующие необязательные поля",
        "disturbed": "6. Возмущения + нормализация ID (п. 1.18)",
        "flow_conn": "7. Реакция на потерю связи (тот же эпизод)",
        "flow_low": "8. Реакция на resource_shortage (тот же эпизод)",
        "flow_multi": "9. Несколько активных сценариев (тот же эпизод)",
        "priority_target": "10. Приоритетная цель",
        "flow_inspect": "11. Директива inspect_point / синтез (тот же эпизод)",
        "flow_stop": "12. Директива safe_stop (тот же эпизод)",
    }
    fixes = {
        "base": "Верните полное решение Listing 5 с валидными командами и объяснением.",
        "dialect_strings": "Поддержите available_actions в виде массива строк.",
        "dialect_object_id": "Поддержите available_actions в виде массива объектов с ключом id.",
        "minimal": "Не требуйте необязательные поля (orientation, trust и др.).",
        "disturbed": "Контроллер должен работать после переименования ID и перемешивания массивов.",
        "flow_conn": "Отработайте потерю связи (switch/relay_coalition или причина в explanation).",
        "flow_low": "Отработайте дефицит энергии (modify/safe_degradation/safe_stop).",
        "flow_multi": "Отработайте несколько активных сценариев (compose/keep).",
        "priority_target": "Отработайте приоритетную цель (select/switch/priority_task).",
        "flow_inspect": "Отработайте директиву inspect_point (при необходимости synthesize).",
        "flow_stop": "При директиве stop/safe_stop верните operation='safe_stop'.",
    }

    base_obs = None
    safe_stop_decision = None
    for name, observation in suite:
        if name in ("undocumented_base", "undocumented_extra"):
            continue
        if name == "base":
            base_obs = observation
        if name == "disturbed":
            observation = PublicNormalizer(seed=99).forward_obs(observation)
        result = run_case(labels.get(name, name), observation, fixes.get(name, "Приведите решение к Listing 5."))
        if name == "flow_stop":
            safe_stop_decision = result

    if isinstance(safe_stop_decision, dict):
        op = safe_stop_decision.get("operation") or (safe_stop_decision.get("meta_decision") or {}).get("operation")
        if op != "safe_stop":
            rep.warn("12b. Реакция на директиву safe_stop",
                     f"решение валидно, но operation={op!r} вместо 'safe_stop'",
                     fix="При обязательной директиве stop/safe_stop верните operation='safe_stop'.")

    # 2b. Повторное наблюдение
    if base_obs is not None:
        repeat_obs = copy.deepcopy(base_obs)
        repeat_obs["episode_id"] = "ep_suite_repeat"
        repeat_obs["request_id"] = "req_repeat"
        c1, d1, r1, _ = send_decide(repeat_obs)
        c2, d2, r2, _ = send_decide(repeat_obs)
        if c1 == 200 and c2 == 200 and isinstance(d1, dict) and isinstance(d2, dict):
            problems, warnings = validate_decision_struct(d2, repeat_obs, strict_axis=True)
            warning_notes.extend(warnings)
            if problems:
                rep.fail("2b. Повторное наблюдение (идемпотентность)", "; ".join(problems[:5]),
                         fix="На повторное наблюдение верните валидное решение Listing 5.")
            else:
                op = d2.get("operation") or (d2.get("meta_decision") or {}).get("operation")
                rep.ok("2b. Повторное наблюдение (идемпотентность)", f"op={op}")
                if op in META_OPERATIONS:
                    observed_ops.add(op)
        else:
            rep.fail("2b. Повторное наблюдение (идемпотентность)", f"HTTP {c1}/{c2}: {r1[:120]} / {r2[:120]}",
                     fix="Повторный decide должен возвращать 200 и валидное решение.")

    # 13. Игнорирование недокументированных полей
    undoc = {name: obs for name, obs in suite if name in ("undocumented_base", "undocumented_extra")}
    if "undocumented_base" in undoc and "undocumented_extra" in undoc:
        plain = copy.deepcopy(undoc["undocumented_extra"])
        plain["episode_id"] = "ep_suite_extra_plain"
        plain.pop("_author_specific", None)
        if isinstance(plain.get("mission"), dict):
            plain["mission"].pop("_author_specific", None)
        if isinstance(plain.get("network"), dict):
            plain["network"].pop("_author_specific", None)
        for agent in plain.get("agents", []):
            if isinstance(agent, dict):
                agent.pop("_author_specific", None)
        c1, d1, r1, _ = send_decide(plain)
        c2, d2, r2, _ = send_decide(undoc["undocumented_extra"])
        if c1 == 200 and c2 == 200 and isinstance(d1, dict) and isinstance(d2, dict):
            p1, w1 = validate_decision_struct(d1, plain, strict_axis=True)
            p2, w2 = validate_decision_struct(d2, undoc["undocumented_extra"], strict_axis=True)
            warning_notes.extend(w1 + w2)
            if p1 or p2:
                rep.fail("13. Игнорирование недокументированных полей", "; ".join((p1 + p2)[:5]),
                         fix="Решение должно быть валидным и не зависеть от полей вне спецификации.")
            elif _decision_signature(d1) != _decision_signature(d2):
                rep.fail("13. Игнорирование недокументированных полей",
                         "решение изменилось после добавления полей _author_specific",
                         fix="Не используйте недокументированные поля среды (п. 1.17, тест 7).")
            else:
                rep.ok("13. Игнорирование недокументированных полей", "добавление неизвестных полей не изменило решение")
        else:
            rep.fail("13. Игнорирование недокументированных полей", f"HTTP {c1}/{c2}: {r1[:120]} / {r2[:120]}",
                     fix="Контроллер должен принимать наблюдение с лишними необязательными полями.")

    # 14. Изоляция эпизодов при смене episode_id
    obs_a = make_synthetic_obs(0, "object_action", "ep_iso_A")
    obs_b = copy.deepcopy(obs_a)
    obs_b["episode_id"] = "ep_iso_B"
    obs_b["agents"][0]["id"] = "other_drone_99"
    obs_b["agents"][1]["id"] = "other_rover_88"
    obs_c = copy.deepcopy(obs_a)
    obs_c["episode_id"] = "ep_iso_C"
    c_a, d_a, _, _ = send_decide(obs_a)
    c_b, d_b, _, _ = send_decide(obs_b)
    c_c, d_c, _, _ = send_decide(obs_c)
    if c_a == c_b == c_c == 200 and all(isinstance(x, dict) for x in (d_a, d_b, d_c)):
        p_a, w_a = validate_decision_struct(d_a, obs_a, strict_axis=True)
        p_b, w_b = validate_decision_struct(d_b, obs_b, strict_axis=True)
        p_c, w_c = validate_decision_struct(d_c, obs_c, strict_axis=True)
        warning_notes.extend(w_a + w_b + w_c)
        leaked = any(x in json.dumps(d_c, ensure_ascii=False) for x in ("other_drone_99", "other_rover_88"))
        if p_a or p_b or p_c or leaked:
            rep.fail("14. Изоляция состояния при смене episode_id",
                     f"утечка старых ID={leaked}, ошибки={(p_a + p_b + p_c)[:5]}",
                     fix="При новом episode_id полностью сбрасывайте карту, историю, роли и убеждения.")
        else:
            rep.ok("14. Изоляция состояния при смене episode_id", "смена episode_id не протекает между эпизодами")
    else:
        rep.fail("14. Изоляция состояния при смене episode_id", f"HTTP коды: {c_a}, {c_b}, {c_c}",
                 fix="decide должен отвечать 200 при переключении episode_id.")

    # 15. Некорректное наблюдение
    c, b, r, _ = send_decide({})
    if c == 404:
        rep.fail("15. Некорректное наблюдение", f"маршрут POST /v1/controller/decide отсутствует (HTTP 404): {r[:180]}",
                 fix="Реализуйте POST /v1/controller/decide.")
    elif 400 <= c < 500:
        err_code = (b.get("error") or {}).get("code") if isinstance(b, dict) else None
        if err_code in ERROR_CODES:
            rep.ok("15. Некорректное наблюдение", f"HTTP {c}, code={err_code}")
        else:
            rep.warn("15. Некорректное наблюдение", f"HTTP {c}, нестандартный code={err_code}",
                     fix="Возвращайте 4xx с нормативным error.code (INVALID_REQUEST и др.).")
    elif c == 200:
        rep.warn("15. Некорректное наблюдение", "контроллер принял пустой объект как наблюдение",
                 fix="Проверяйте обязательные поля observation и возвращайте 4xx INVALID_REQUEST.")
    else:
        rep.fail("15. Некорректное наблюдение", f"HTTP {c}, body={r[:200]}",
                 fix="На некорректное наблюдение возвращайте 4xx, не 500.")

    # 16. Покрытие операций
    missing_ops = [op for op in META_OPERATIONS if op not in observed_ops]
    if missing_ops:
        rep.warn("16. Покрытие операций в тестах",
                 f"в ответах не встречены: {missing_ops}, observed={sorted(observed_ops)}",
                 fix="Убедитесь, что контроллер умеет все 7 операций и отражает их в журнале.")
    else:
        rep.ok("16. Покрытие операций в тестах", "все 7 операций встречены")

    # 17. Журнал решений
    c, _, r, _ = http_json(base_url, "GET", "/v1/controller/logs", timeout=timeout)
    if c == 200 and r.strip():
        parsed = False
        try:
            json.loads(r)
            parsed = True
        except Exception:
            try:
                lines = [x for x in r.splitlines() if x.strip()]
                if lines:
                    for line in lines:
                        json.loads(line)
                    parsed = True
            except Exception:
                parsed = False
        if parsed:
            rep.ok("17. GET /v1/controller/logs", "машиночитаемый журнал решений доступен")
        else:
            rep.warn("17. GET /v1/controller/logs", "ответ не парсится как JSON/JSONL",
                     fix="Верните журнал решений в JSON Lines или валидном JSON.")
    else:
        rep.warn("17. GET /v1/controller/logs", f"HTTP {c} (маршрут не обязателен, но рекомендуется)",
                 fix="Добавьте GET /v1/controller/logs с журналом решений для упрощения аудита M6.")

    # 18. Замечания к решениям
    if warning_notes:
        unique = []
        for note in warning_notes:
            if note not in unique:
                unique.append(note)
        rep.warn("18. Замечания к решениям",
                 "; ".join(unique[:6]) + (" ..." if len(unique) > 6 else ""),
                 fix="Используйте passed/failed/unknown в constraint_checks.status и заполняйте explanation.")
    return rep


def check_pair(env_url, ctrl_url, seed=42, max_steps=20, min_steps=DEFAULT_MIN_STEPS,
               normalize=False, timeout=15.0, strict=False):
    mode_tag = "с нормализацией п.1.18" if normalize else "прямой"
    rep = Report(f"Стыковка пары ({mode_tag}): ctrl={ctrl_url} -> env={env_url}, seed={seed}", strict=strict)
    pair_tag = uuid.uuid4().hex[:8]

    c_e, e_health, r_e, _ = http_json(env_url, "GET", "/v1/environment/health", timeout=timeout)
    c_c, c_health, r_c, _ = http_json(ctrl_url, "GET", "/v1/controller/health", timeout=timeout)
    env_ok = c_e == 200 and isinstance(e_health, dict) and e_health.get("status") == "ready" and e_health.get("protocol_version") == PROTOCOL_VERSION
    ctrl_ok = c_c == 200 and isinstance(c_health, dict) and c_health.get("status") == "ready" and c_health.get("protocol_version") == PROTOCOL_VERSION
    if not env_ok or not ctrl_ok:
        rep.fail("Готовность пары", f"env={c_e} ({r_e[:100]}), ctrl={c_c} ({r_c[:100]})",
                 fix="Оба компонента должны отвечать на health со status='ready' и protocol_version='1.0'.")
        return rep
    rep.ok("Готовность пары", "health среды и контроллера корректны")

    reset_req = {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": f"pair_{RUN_TAG}_{pair_tag}_reset_{seed}",
        "seed": seed,
        "mission": {"id": "search_and_rescue", "parameters": {}},
    }
    code, rb, raw, _ = http_json(env_url, "POST", "/v1/environment/reset", reset_req, timeout=timeout)
    if code != 200 or not isinstance(rb, dict) or not rb.get("episode_id"):
        rep.fail("Сброс эпизода", f"HTTP {code}: {raw[:220]}",
                 fix="Среда должна принимать reset и возвращать episode_id/observation (Listing 10).")
        return rep

    ep_id = rb["episode_id"]
    obs = unwrap_obs(rb)
    obs_problems, _ = validate_observation(obs)
    if obs_problems:
        rep.fail("Стартовое наблюдение", "; ".join(obs_problems[:6]),
                 fix="Среда должна вернуть полное observation Табл. 9 в ответе reset.")
        return rep

    norm = PublicNormalizer(seed=seed) if normalize else None
    step_times = []
    steps = 0
    commands_sent = 0
    accepted = rejected = unknown = 0
    command_results_seen = False
    time_advanced = 0
    done_status = "running"
    terminal_ok = False
    terminal_stopped = False
    failure = None
    start_time = obs.get("time", 0.0)

    for s in range(1, max_steps + 1):
        obs_for_ctrl = norm.forward_obs(obs) if norm else obs
        ctrl_problems, _ = validate_observation(obs_for_ctrl)
        if ctrl_problems:
            failure = f"среда вернула неполное наблюдение: {'; '.join(ctrl_problems[:4])}"
            break

        t0 = time.monotonic()
        c_d, dec, r_d, _ = http_json(ctrl_url, "POST", "/v1/controller/decide",
                                     {"observation": obs_for_ctrl}, timeout=timeout)
        if c_d != 200 or not isinstance(dec, dict):
            failure = f"контроллер не вернул решение: HTTP {c_d}: {r_d[:220]}"
            break

        problems, _ = validate_decision_struct(dec, obs_for_ctrl)
        if problems:
            failure = "контроллер вернул некорректное решение: " + "; ".join(problems[:6])
            break

        dec_for_env = norm.backward_decision(dec) if norm else copy.deepcopy(dec)
        dec_for_env["protocol_version"] = PROTOCOL_VERSION
        dec_for_env["episode_id"] = ep_id
        dec_for_env["request_id"] = f"pair_{RUN_TAG}_{pair_tag}_step_{seed}_{s}"
        dec_for_env["time"] = obs.get("time", 0.0)

        c_s, sb, r_s, _ = http_json(env_url, "POST", "/v1/environment/step", dec_for_env, timeout=timeout)
        step_times.append(time.monotonic() - t0)
        if c_s != 200 or not isinstance(sb, dict):
            failure = f"среда отклонила решение: HTTP {c_s}: {r_s[:260]}"
            break

        new_obs = unwrap_obs(sb)
        new_time = sb.get("time", new_obs.get("time"))
        env_problems, _ = validate_observation(new_obs)
        if env_problems:
            failure = f"среда вернула неполное observation после step: {'; '.join(env_problems[:4])}"
            break
        if _is_num(new_time) and _is_num(obs.get("time")) and new_time > obs.get("time") + 1e-9:
            time_advanced += 1

        sent = dec_for_env.get("commands") if isinstance(dec_for_env.get("commands"), list) else []
        commands_sent += len(sent)
        command_results = sb.get("command_results")
        if isinstance(command_results, list) and command_results:
            command_results_seen = True
        a, r, u = command_result_counts(command_results)
        accepted += a
        rejected += r
        unknown += u

        obs = new_obs
        steps = s
        done_status = str(sb.get("status", "running")).lower()
        done_flag = bool(sb.get("done"))

        if done_flag or done_status in ("success", "completed", "failed", "safe_stopped", "time_limit"):
            if done_status in ("success", "completed"):
                terminal_ok = True
            elif done_status == "time_limit":
                # Штатное завершение по лимиту времени после >= min_steps допустимо.
                if s >= min_steps:
                    terminal_ok = True
                else:
                    failure = f"эпизод завершился time_limit слишком рано (шаг {s} < min_steps={min_steps})"
            elif done_status == "safe_stopped":
                if s == 1 and not has_stop_directive(obs_for_ctrl):
                    failure = "контроллер ушёл в safe_stop на шаге 1 без директивы остановки"
                elif s < min_steps and not has_stop_directive(obs_for_ctrl):
                    failure = f"пара завершилась safe_stopped на шаге {s} < min_steps={min_steps} без директивы"
                else:
                    terminal_stopped = True
            elif done_status == "failed":
                failure = f"эпизод завершился аварийно (failed) на шаге {s}"
            break

    http_json(env_url, "POST", "/v1/environment/close",
              {"protocol_version": PROTOCOL_VERSION, "episode_id": ep_id,
               "request_id": f"pair_{RUN_TAG}_{pair_tag}_close_{seed}"}, timeout=timeout)

    if failure:
        text = failure.lower()
        hints = []
        if "decide" in text or "404" in text:
            hints.append("Реализуйте POST /v1/controller/decide.")
        if "_optional_hint" in text or "_ref_optional" in text:
            hints.append("Среда должна игнорировать неизвестные необязательные поля в commands[].parameters (п. 1.13.1).")
        if "constraint_checks" in text:
            hints.append("Среда не должна падать на строковых статусах в constraint_checks.")
        if "outside" in text or "surface" in text or "ground" in text or "зон" in text:
            hints.append("Проверьте оси [x,y,z] с высотой z и границы зон.")
        if "safe_stop" in text:
            hints.append("Контроллер не должен уходить в safe_stop без директивы остановки.")
        if not hints:
            hints.append("Проверьте публичные поля, ID и соответствие Listing 5.")
        rep.fail("Стыковочный прогон", failure,
                 fix="Пара несовместима. " + " ".join(hints) + " Локализуйте через --ref-env/--ref-ctrl.")
        return rep

    if steps < min_steps and not terminal_ok and not terminal_stopped:
        rep.fail("Стыковочный прогон", f"выполнено шагов={steps}, меньше min_steps={min_steps}",
                 fix="Проверьте, почему эпизод завершился раньше min_steps.")
        return rep
    if time_advanced == 0 and not terminal_stopped:
        rep.fail("Стыковочный прогон", "модельное время ни разу не продвинулось",
                 fix="Среда обязана продвигать time на каждом шаге.")
        return rep
    if commands_sent == 0 and not terminal_ok and not terminal_stopped:
        rep.fail("Стыковочный прогон", "контроллер не отправил ни одной команды за весь прогон",
                 fix="Контроллер должен выдавать допустимые команды агентам.")
        return rep
    if rejected > 0 and accepted == 0 and not terminal_ok and not terminal_stopped:
        rep.fail("Стыковочный прогон", f"все команды отклонены (rejected={rejected}, accepted=0)",
                 fix="Проверьте допустимость commands[].action и координат target.")
        return rep
    if commands_sent > 0 and not command_results_seen:
        rep.fail("Стыковочный прогон", f"среда не вернула command_results при {commands_sent} командах",
                 fix="Возвращайте command_results в ответе step (Listing 11).")
        return rep

    if unknown > 0 and accepted == 0:
        rep.warn("Команды и command_results", f"среда не вернула accepted-статусы (unknown={unknown})",
                 fix="Возвращайте статусы accepted/rejected в command_results.")

    avg_ms = (sum(step_times) / max(1, len(step_times))) * 1000
    rep.ok(
        "Стыковочный прогон",
        f"шагов={steps}, команд={commands_sent}, accepted={accepted}, rejected={rejected}, "
        f"time={start_time}->{obs.get('time')}, итог={done_status}, среднее={avg_ms:.0f} мс/шаг",
    )
    return rep


# ---------------------------------------------------------------------------
# Эталонные тестовые двойники для самопроверки команд (не турнирные решения).
# ---------------------------------------------------------------------------

class RefError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message

    def payload(self, request_id=None, episode_id=None):
        return {
            "protocol_version": PROTOCOL_VERSION,
            "request_id": request_id,
            "episode_id": episode_id,
            "error": {"code": self.code, "message": self.message},
        }


class RefEnvironment:
    """Минимальная Z-up среда: 2 агента, 4 объекта инфраструктуры, 1 цель."""

    def __init__(self):
        self.lock = threading.Lock()
        self.counter = 0
        self.episode_id = None
        self.obs = None
        self.time = 0.0
        self.status = "running"
        self.closed = False
        self.records = []

    def health(self):
        return {
            "protocol_version": PROTOCOL_VERSION,
            "component": "environment",
            "status": "ready",
            "environment_id": "ref_environment",
            "capabilities_url": "/v1/environment/capabilities",
        }

    def capabilities(self):
        return {
            "protocol_version": PROTOCOL_VERSION,
            "environment_id": "ref_environment",
            "agent_kinds": ["aerial", "ground", "underground", "surface_water",
                            "underwater", "infrastructure", "digital", "operator"],
            "actions": ["move", "observe", "relay", "deliver", "charge", "rescue"],
            "zone_types": ["land", "air", "restricted", "water", "underground",
                           "obstacle", "hazard", "low_connectivity", "infrastructure"],
            "scenarios": [{"id": sid, "version": "1.0"} for sid in FALLBACK_SCENARIOS],
            "supported_operations": list(META_OPERATIONS),
            "coordinates": {"unit": "meter", "axes": ["x", "y", "z"],
                            "internal_step": 1.0, "external_step": 1.0},
            "extensions": [],
        }

    def reset(self, request):
        with self.lock:
            if not isinstance(request, dict):
                raise RefError(400, "INVALID_REQUEST", "Expected JSON object")
            seed = request.get("seed", 0)
            if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
                raise RefError(400, "INVALID_REQUEST", "Expected non-negative integer seed")
            mission = request.get("mission") or {}
            if not isinstance(mission, dict):
                raise RefError(400, "INVALID_REQUEST", "mission must be an object")
            self.counter += 1
            self.episode_id = f"ref_ep_{seed}_{self.counter}"
            self.obs = make_synthetic_obs(0, "object_action", self.episode_id)
            self.obs["request_id"] = request.get("request_id", "ref_reset")
            self.obs["time"] = 0.0
            self.obs["metrics"] = {"mission_completion": 0.0, "safety_violations": 0, "step_count": 0}
            self.obs["mission"]["status"] = "active"
            self.time = 0.0
            self.status = "running"
            self.closed = False
            self.records = [{
                "record_id": "ref_rec_0000",
                "episode_id": self.episode_id,
                "time": 0.0,
                "kind": "reset",
                "mission_id": mission.get("id", "search_and_rescue"),
            }]
            return {
                "protocol_version": PROTOCOL_VERSION,
                "request_id": self.obs["request_id"],
                "episode_id": self.episode_id,
                "time": 0.0,
                "status": "running",
                "observation": copy.deepcopy(self.obs),
            }

    def observation(self):
        with self.lock:
            if self.closed or not isinstance(self.obs, dict):
                raise RefError(409, "UNKNOWN_EPISODE", "Reset an active episode first")
            return {
                "protocol_version": PROTOCOL_VERSION,
                "request_id": self.obs.get("request_id"),
                "episode_id": self.episode_id,
                "time": self.time,
                "status": self.status,
                "observation": copy.deepcopy(self.obs),
            }

    @staticmethod
    def _reject_command(agent_id, action, message):
        return {"agent_id": agent_id, "action": action, "status": "rejected",
                "error": {"code": "CONSTRAINT_VIOLATION", "message": message}}

    def step(self, decision):
        with self.lock:
            if self.closed or not isinstance(self.obs, dict):
                raise RefError(409, "UNKNOWN_EPISODE", "Reset an active episode first")
            if not isinstance(decision, dict):
                raise RefError(400, "INVALID_REQUEST", "Decision must be an object")
            if decision.get("protocol_version") not in (None, PROTOCOL_VERSION):
                raise RefError(422, "UNSUPPORTED_PROTOCOL_VERSION", "Expected protocol 1.0")
            if decision.get("episode_id") != self.episode_id:
                raise RefError(409, "UNKNOWN_EPISODE", "Decision episode_id does not match active episode")
            operation = decision.get("operation") or (decision.get("meta_decision") or {}).get("operation")
            if operation not in META_OPERATIONS:
                raise RefError(422, "INVALID_REQUEST", f"Unsupported operation {operation!r}")
            commands = decision.get("commands", [])
            if not isinstance(commands, list):
                raise RefError(400, "INVALID_REQUEST", "commands must be an array")

            agents = {a["id"]: a for a in self.obs.get("agents", [])
                      if isinstance(a, dict) and isinstance(a.get("id"), str)}
            results = []
            for cmd in commands:
                if not isinstance(cmd, dict):
                    results.append({"status": "rejected", "error": {"code": "INVALID_COMMAND", "message": "Command must be an object"}})
                    continue
                aid = cmd.get("agent_id")
                action = cmd.get("action")
                agent = agents.get(aid)
                if agent is None:
                    results.append(self._reject_command(aid, action, f"Unknown agent {aid}"))
                    continue
                available = {action_name(x) for x in agent.get("available_actions", [])}
                if action not in available:
                    results.append(self._reject_command(aid, action, f"Action {action} is unavailable for {aid}"))
                    continue
                params = cmd.get("parameters") if isinstance(cmd.get("parameters"), dict) else {}
                if action == "move":
                    target = params.get("target")
                    if not finite_vec3(target):
                        results.append(self._reject_command(aid, action, "move requires parameters.target=[x,y,z]"))
                        continue
                    kind = agent.get("kind")
                    x, y, z = target
                    if not (0.0 <= x <= 200.0 and 0.0 <= y <= 200.0):
                        results.append(self._reject_command(aid, action, "Target outside workspace"))
                        continue
                    if kind in ("ground", "surface_water", "underground") and not (-0.5 <= z <= 2.0):
                        results.append(self._reject_command(aid, action, "Ground agent must stay on surface (-0.5<=z<=2.0)"))
                        continue
                    if kind == "aerial" and not (0.0 <= z <= 100.0):
                        results.append(self._reject_command(aid, action, "Aerial target outside air zone (0<=z<=100)"))
                        continue
                    if 80.0 <= x <= 100.0 and 80.0 <= y <= 100.0 and z <= 50.0:
                        results.append(self._reject_command(aid, action, "Target is inside restricted zone"))
                        continue
                    agent["position"] = [round(float(x), 3), round(float(y), 3), round(float(z), 3)]
                results.append({"agent_id": aid, "action": action, "status": "accepted"})

            self.time = round(self.time + 1.0, 4)
            self.obs["time"] = self.time
            self.obs["request_id"] = decision.get("request_id", "ref_step")
            self.obs["metrics"]["step_count"] = self.obs["metrics"].get("step_count", 0) + 1

            target = next((t for t in self.obs.get("targets", []) if isinstance(t, dict)), None)
            ground = next((a for a in self.obs.get("agents", [])
                           if isinstance(a, dict) and a.get("kind") == "ground"), None)
            if target and ground and math.dist(ground["position"], target["position"]) <= 2.0:
                self.obs["metrics"]["mission_completion"] = 1.0
                self.obs["mission"]["status"] = "completed"
                self.status = "success" if self.status == "running" else self.status
            if operation == "safe_stop":
                self.status = "safe_stopped"
            elif self.time >= 60.0 and self.status == "running":
                self.status = "time_limit"

            self.records.append({
                "record_id": f"ref_rec_{len(self.records):04d}",
                "episode_id": self.episode_id,
                "time": self.time,
                "kind": "step",
                "operation": operation,
                "selected_scenarios": (decision.get("selected_configuration") or {}).get("scenario_ids", []),
                "commands": copy.deepcopy(commands),
                "command_results": copy.deepcopy(results),
                "metrics": copy.deepcopy(self.obs["metrics"]),
            })
            return {
                "protocol_version": PROTOCOL_VERSION,
                "request_id": decision.get("request_id"),
                "episode_id": self.episode_id,
                "time": self.time,
                "status": self.status,
                "done": self.status in ("success", "failed", "safe_stopped", "time_limit"),
                "command_results": results,
                "events": [],
                "metrics": copy.deepcopy(self.obs["metrics"]),
                "observation": copy.deepcopy(self.obs),
            }

    def logs(self):
        with self.lock:
            return {"protocol_version": PROTOCOL_VERSION, "episode_id": self.episode_id,
                    "records": copy.deepcopy(self.records)}

    def close(self, request=None):
        with self.lock:
            if not self.episode_id:
                raise RefError(409, "UNKNOWN_EPISODE", "No active episode")
            if isinstance(request, dict) and request.get("episode_id") not in (None, self.episode_id):
                raise RefError(409, "UNKNOWN_EPISODE", "Unknown episode")
            self.closed = True
            self.status = "closed"
            return {"protocol_version": PROTOCOL_VERSION, "episode_id": self.episode_id, "status": "closed"}

    def handle(self, method, path, payload):
        if method == "GET" and path == "/v1/environment/health":
            return 200, self.health()
        if method == "GET" and path == "/v1/environment/capabilities":
            return 200, self.capabilities()
        if method == "GET" and path == "/v1/environment/observation":
            return 200, self.observation()
        if method == "GET" and path == "/v1/environment/logs":
            return 200, self.logs()
        if method == "POST" and path == "/v1/environment/reset":
            return 200, self.reset(payload)
        if method == "POST" and path == "/v1/environment/step":
            return 200, self.step(payload)
        if method == "POST" and path == "/v1/environment/close":
            return 200, self.close(payload)
        raise RefError(404, "INVALID_REQUEST", "Unknown route")


class RefController:
    """Минимальный переносимый контроллер: подстраивается под границы зон среды,
    сохраняет высоту рельефа для наземных агентов, читает все 3 диалекта и умеет 7 операций."""

    def __init__(self, preferred_scenarios=None):
        self.lock = threading.Lock()
        self.episodes = {}
        self.records = []
        self.preferred_scenarios = list(preferred_scenarios or [])

    def health(self):
        return {
            "protocol_version": PROTOCOL_VERSION,
            "component": "controller",
            "status": "ready",
            "controller_id": "ref_controller",
            "supported_operations": list(META_OPERATIONS),
        }

    @staticmethod
    def _inspect_requested(obs):
        mission = obs.get("mission") if isinstance(obs.get("mission"), dict) else {}
        for d in mission.get("directives", []) if isinstance(mission.get("directives"), list) else []:
            if isinstance(d, dict) and str(d.get("type") or "").lower() in ("inspect_point", "inspect", "standoff"):
                return True
        return False

    @staticmethod
    def _target_for(agent, index, obs):
        """Безопасный небольшой сдвиг в пределах зон среды с сохранением z рельефа."""
        p = agent.get("position")
        if not finite_vec3(p):
            return None
        boxes = _zone_boxes(obs)
        restricted = [(mn, mx) for zt, mn, mx in boxes if zt in ("restricted", "obstacle")]
        valid_zones = [(mn, mx) for zt, mn, mx in boxes if zt not in ("restricted", "obstacle")]

        def allowed(pt):
            if any(_point_in_box(pt, mn, mx) for mn, mx in restricted):
                return False
            if valid_zones and not any(_point_in_box(pt, mn, mx) for mn, mx in valid_zones):
                return False
            return True

        step_xy = 1.5 + 0.5 * (index % 3)
        # Пробуем малый горизонтальный сдвиг, сохраняя текущую высоту p[2]
        # (не загоняем наземных агентов под рельеф вроде Хибин и не выходим за маленькие карты).
        candidates = [
            [p[0] + step_xy, p[1], p[2]],
            [p[0] - step_xy, p[1], p[2]],
            [p[0], p[1] + step_xy, p[2]],
            [p[0], p[1] - step_xy, p[2]],
            [p[0], p[1], p[2]],
        ]
        for cand in candidates:
            pt = [round(float(c), 3) for c in cand]
            if allowed(pt):
                return pt
        return [round(float(c), 3) for c in p]

    def decide(self, body):
        obs = body.get("observation", body) if isinstance(body, dict) else None
        if not isinstance(obs, dict) or not isinstance(obs.get("agents"), list):
            raise RefError(422, "INVALID_REQUEST", "Invalid public observation")
        if obs.get("protocol_version") not in (None, PROTOCOL_VERSION):
            raise RefError(422, "UNSUPPORTED_PROTOCOL_VERSION", "Expected protocol 1.0")
        episode_id = obs.get("episode_id")
        if not isinstance(episode_id, str) or not _is_num(obs.get("time")):
            raise RefError(422, "INVALID_REQUEST", "episode_id/time are required")

        with self.lock:
            if episode_id not in self.episodes:
                self.episodes[episode_id] = {"ids": [], "roles": {}, "first": True, "counter": 0}
            state = self.episodes[episode_id]
            state["counter"] += 1
            prev_ids = list(state["ids"])
            prev_roles = dict(state["roles"])
            agents = [a for a in obs.get("agents", [])
                      if isinstance(a, dict) and isinstance(a.get("id"), str)
                      and a.get("status") == "active"]
            active_scenarios = []
            for a in agents:
                for sid in a.get("scenario_ids", []) if isinstance(a.get("scenario_ids"), list) else []:
                    if isinstance(sid, str) and sid not in active_scenarios:
                        active_scenarios.append(sid)
            events = {e.get("type") for e in obs.get("events", []) if isinstance(e, dict)}
            network = obs.get("network") if isinstance(obs.get("network"), dict) else {}
            min_energy = min((_energy_of(a) for a in agents), default=1.0)

            sc_primary = self.preferred_scenarios[0] if self.preferred_scenarios else "coverage_search"
            sc_relay = (self.preferred_scenarios[1] if len(self.preferred_scenarios) > 1
                        else "relay_coalition")

            if has_stop_directive(obs):
                op, ids, params, synth = "safe_stop", (prev_ids or [sc_primary]), {}, None
            elif len(active_scenarios) > 1:
                op, ids, params, synth = "compose", [sc_primary, sc_relay], {}, None
            elif self._inspect_requested(obs):
                op, ids, params, synth = "synthesize", [MIN_SYNTH_SCENARIO["id"]], {}, copy.deepcopy(MIN_SYNTH_SCENARIO)
            elif ("connectivity_loss" in events
                  or float(network.get("connectivity_score", 1.0) or 1.0) < 0.6):
                op, ids, params, synth = "switch", [sc_relay], {}, None
            elif "resource_shortage" in events or min_energy < 0.2:
                op, ids, params, synth = "modify", (prev_ids or [sc_primary]), {"priority_bias": 1.1}, None
            elif state["first"] or not prev_ids:
                op, ids, params, synth = "select", [sc_primary], {}, None
            else:
                op, ids, params, synth = "keep", prev_ids, {}, None

            roles = {}
            for a in agents:
                if finite_vec3(a.get("position")):
                    roles[a["id"]] = "scout" if a.get("kind") == "aerial" else "executor"
            commands = []
            if op != "safe_stop":
                movable = [a for a in agents
                           if finite_vec3(a.get("position"))
                           and "move" in {action_name(x) for x in a.get("available_actions", [])}]
                for index, agent in enumerate(movable[:2]):
                    target = self._target_for(agent, index, obs)
                    if target is None:
                        continue
                    parameters = {"target": target}
                    if index == 0:
                        # Проверяет требование п. 1.13.1 игнорировать неизвестные необязательные поля.
                        parameters["_ref_optional"] = "ignore-me"
                    commands.append({"agent_id": agent["id"], "action": "move", "parameters": parameters})

            decision = {
                "protocol_version": PROTOCOL_VERSION,
                "episode_id": episode_id,
                "decision_id": f"ref_{RUN_TAG}_decision_{state['counter']:04d}",
                "request_id": f"ref_{RUN_TAG}_request_{state['counter']:04d}",
                "time": float(obs.get("time", 0.0)),
                "operation": op,
                "previous_configuration": {"scenario_ids": prev_ids, "role_assignments": prev_roles},
                "selected_configuration": {
                    "scenario_ids": ids,
                    "parameters": params,
                    "synthesized_scenario": synth,
                },
                "candidate_evaluations": [{
                    "scenario_id": ids[0],
                    "applicable": True,
                    "utility": 0.8,
                    "risk": 0.1,
                    "selected": True,
                }],
                "role_assignments": roles,
                "commands": commands,
                "constraint_checks": [
                    {"constraint_id": "minimum_energy_reserve", "status": "passed"},
                    {"constraint_id": "public_route", "status": "unknown"},
                ],
                "explanation": {
                    "observation_refs": [episode_id, f"time:{obs.get('time')}"],
                    "reason_codes": [f"ref_{op}"],
                    "expected_effects": ["maintain_public_contract"],
                    "confidence": 0.9,
                },
            }
            state["ids"] = ids
            state["roles"] = roles
            state["first"] = False
            self.records.append(copy.deepcopy(decision))
            return decision

    def handle(self, method, path, payload):
        if method == "GET" and path == "/v1/controller/health":
            return 200, self.health()
        if method == "GET" and path == "/v1/controller/logs":
            return 200, {"protocol_version": PROTOCOL_VERSION, "records": copy.deepcopy(self.records)}
        if method == "POST" and path == "/v1/controller/decide":
            return 200, self.decide(payload)
        raise RefError(404, "INVALID_REQUEST", "Unknown route")


def _energy_of(agent):
    resources = agent.get("resources") if isinstance(agent.get("resources"), dict) else {}
    energy = resources.get("energy")
    value = energy.get("value") if isinstance(energy, dict) else energy
    return float(value) if _is_num(value) else 1.0


class _RefHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, app):
        self.app = app
        super().__init__(address, _RefHandler)


class _RefHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def _send(self, status, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _handle(self, method):
        parsed = urllib.parse.urlsplit(self.path)
        payload = None
        if method == "POST":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length) if length else b""
                payload = json.loads(body.decode("utf-8")) if body else {}
            except Exception:
                self._send(400, RefError(400, "INVALID_REQUEST", "Malformed JSON").payload())
                return
        try:
            status, result = self.server.app.handle(method, parsed.path, payload)
            if isinstance(result, dict) and "protocol_version" not in result:
                result = {"protocol_version": PROTOCOL_VERSION, **result}
            self._send(status, result)
        except RefError as error:
            request_id = payload.get("request_id") if isinstance(payload, dict) else None
            episode_id = payload.get("episode_id") if isinstance(payload, dict) else None
            self._send(error.status, error.payload(request_id, episode_id))
        except Exception as error:
            self._send(500, RefError(500, "INTERNAL_ERROR", str(error)).payload())

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")


def start_ref(app, host="127.0.0.1", port=0):
    server = _RefHTTPServer((host, port), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://{host}:{server.server_address[1]}"


def stop_ref(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def _ref_ctrl_for_env(env_url, timeout=10.0):
    """Создаёт RefController с учётом сценариев из capabilities целевой среды."""
    code, caps, _, _ = http_json(env_url, "GET", "/v1/environment/capabilities", timeout=timeout)
    preferred = extract_declared_scenarios(caps) if code == 200 else []
    return RefController(preferred_scenarios=preferred)


def check_selftest(env_url=None, ctrl_url=None, seed=42, max_steps=20,
                   min_steps=DEFAULT_MIN_STEPS, timeout=15.0, strict=False):
    if not env_url and not ctrl_url:
        raise ValueError("selftest требует --env-url и/или --ctrl-url")

    reports = []
    if env_url:
        reports.append(("env", check_environment(env_url, timeout=timeout, strict=strict)))
    if ctrl_url:
        reports.append(("ctrl", check_controller(ctrl_url, timeout=timeout, strict=strict)))
    if env_url and ctrl_url:
        reports.append(("pair", check_pair(env_url, ctrl_url, seed=seed, max_steps=max_steps,
                                           min_steps=min_steps, normalize=True,
                                           timeout=timeout, strict=strict)))
    if ctrl_url:
        srv, thr, ref_env_url = start_ref(RefEnvironment())
        try:
            reports.append(("pair/ref-env", check_pair(ref_env_url, ctrl_url, seed=seed,
                                                       max_steps=max_steps, min_steps=min_steps,
                                                       normalize=True, timeout=timeout, strict=strict)))
        finally:
            stop_ref(srv, thr)
    if env_url:
        srv, thr, ref_ctrl_url = start_ref(_ref_ctrl_for_env(env_url, timeout=timeout))
        try:
            reports.append(("pair/ref-ctrl", check_pair(env_url, ref_ctrl_url, seed=seed,
                                                        max_steps=max_steps, min_steps=min_steps,
                                                        normalize=True, timeout=timeout, strict=strict)))
        finally:
            stop_ref(srv, thr)

    combined = Report("Самопроверка решения (env + ctrl + эталонные пары)", strict=strict)
    for prefix, report in reports:
        for item in report.items:
            combined.add(f"[{prefix}] {item['check']}", item["status"], item["detail"], item.get("fix", ""))
    return combined


def check_refs(strict=True):
    """Служебная проверка встроенных эталонов."""
    reports = []
    srv_e, thr_e, ref_env_url = start_ref(RefEnvironment())
    srv_c, thr_c, ref_ctrl_url = start_ref(RefController())
    try:
        reports.append(("ref-env", check_environment(ref_env_url, timeout=10.0, strict=strict)))
        reports.append(("ref-ctrl", check_controller(ref_ctrl_url, timeout=10.0, strict=strict)))
        reports.append(("ref-pair", check_pair(ref_env_url, ref_ctrl_url, seed=42, max_steps=20,
                                               min_steps=DEFAULT_MIN_STEPS, normalize=True,
                                               timeout=10.0, strict=strict)))
    finally:
        stop_ref(srv_e, thr_e)
        stop_ref(srv_c, thr_c)
    combined = Report("Проверка эталонных компонентов", strict=strict)
    for prefix, report in reports:
        for item in report.items:
            combined.add(f"[{prefix}] {item['check']}", item["status"], item["detail"], item.get("fix", ""))
    return combined


def main():
    parser = argparse.ArgumentParser(
        description="Чекер технического допуска к турниру «Метарой 1.0» и самопроверки команд. "
                    "Чекер не запускает ваши env/ctrl - поднимите их заранее.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Примеры:\n"
            "  python3 check_tournament.py selftest --env-url http://127.0.0.1:8000 "
            "--ctrl-url http://127.0.0.1:8100 --max-steps 10\n"
            "  python3 check_tournament.py pair --ctrl-url http://127.0.0.1:8100 --ref-env --normalize\n"
            "  python3 check_tournament.py pair --env-url http://127.0.0.1:8000 --ref-ctrl --normalize\n"
            "  python3 check_tournament.py env --url http://127.0.0.1:8000 --strict\n"
            "  python3 check_tournament.py ctrl --url http://127.0.0.1:8100 --strict\n"
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_common(sp, timeout):
        sp.add_argument("--timeout", type=float, default=timeout)
        sp.add_argument("--json-out", default=None, help="сохранить отчёт в JSON")
        sp.add_argument("--strict", action="store_true", help="считать WARN ошибкой")

    pe = sub.add_parser("env", help="Проверить среду моделирования")
    pe.add_argument("--url", required=True)
    add_common(pe, 10.0)

    pc = sub.add_parser("ctrl", help="Проверить метасценарный контроллер")
    pc.add_argument("--url", required=True)
    add_common(pc, 10.0)

    pp = sub.add_parser("pair", help="Проверить стыковку пары (среда + контроллер)")
    pp.add_argument("--env-url")
    pp.add_argument("--ctrl-url")
    pp.add_argument("--ref-env", action="store_true", help="поднять встроенную эталонную среду")
    pp.add_argument("--ref-ctrl", action="store_true", help="поднять встроенный эталонный контроллер")
    pp.add_argument("--seed", type=int, default=42)
    pp.add_argument("--max-steps", type=int, default=20)
    pp.add_argument("--min-steps", type=int, default=DEFAULT_MIN_STEPS)
    pp.add_argument("--normalize", action="store_true", help="включить нормализацию п. 1.18")
    add_common(pp, 15.0)

    ps = sub.add_parser("selftest", help="Полная самопроверка: env/ctrl/pair + эталонные пары")
    ps.add_argument("--env-url")
    ps.add_argument("--ctrl-url")
    ps.add_argument("--seed", type=int, default=42)
    ps.add_argument("--max-steps", type=int, default=20)
    ps.add_argument("--min-steps", type=int, default=DEFAULT_MIN_STEPS)
    add_common(ps, 15.0)

    pr = sub.add_parser("refcheck", help="Служебная проверка эталонных компонентов")
    add_common(pr, 10.0)

    pse = sub.add_parser("serve-ref-env", help="Запустить эталонную среду как HTTP-сервер")
    pse.add_argument("--host", default="127.0.0.1")
    pse.add_argument("--port", type=int, default=0)

    psc = sub.add_parser("serve-ref-ctrl", help="Запустить эталонный контроллер как HTTP-сервер")
    psc.add_argument("--host", default="127.0.0.1")
    psc.add_argument("--port", type=int, default=0)

    args = parser.parse_args()

    if args.cmd in ("serve-ref-env", "serve-ref-ctrl"):
        app = RefEnvironment() if args.cmd == "serve-ref-env" else RefController()
        srv, thr, url = start_ref(app, args.host, args.port)
        print(f"Эталонный {args.cmd.split('-')[-1]}: {url}")
        print("Остановить: Ctrl+C")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        finally:
            stop_ref(srv, thr)
        return 0

    rep = None
    started = []
    try:
        if args.cmd == "env":
            rep = check_environment(args.url, timeout=args.timeout, strict=args.strict)
        elif args.cmd == "ctrl":
            rep = check_controller(args.url, timeout=args.timeout, strict=args.strict)
        elif args.cmd == "pair":
            if args.ref_env and args.env_url:
                parser.error("--ref-env несовместим с --env-url")
            if args.ref_ctrl and args.ctrl_url:
                parser.error("--ref-ctrl несовместим с --ctrl-url")
            if not args.env_url and not args.ref_env:
                parser.error("нужен --env-url или --ref-env")
            if not args.ctrl_url and not args.ref_ctrl:
                parser.error("нужен --ctrl-url или --ref-ctrl")
            env_url, ctrl_url = args.env_url, args.ctrl_url
            if args.ref_env:
                srv, thr, env_url = start_ref(RefEnvironment())
                started.append((srv, thr))
            if args.ref_ctrl:
                srv, thr, ctrl_url = start_ref(_ref_ctrl_for_env(env_url, timeout=args.timeout))
                started.append((srv, thr))
            rep = check_pair(env_url, ctrl_url, seed=args.seed, max_steps=args.max_steps,
                             min_steps=args.min_steps, normalize=args.normalize,
                             timeout=args.timeout, strict=args.strict)
        elif args.cmd == "selftest":
            if not args.env_url and not args.ctrl_url:
                parser.error("selftest требует --env-url и/или --ctrl-url")
            rep = check_selftest(args.env_url, args.ctrl_url, seed=args.seed,
                                 max_steps=args.max_steps, min_steps=args.min_steps,
                                 timeout=args.timeout, strict=args.strict)
        elif args.cmd == "refcheck":
            rep = check_refs(strict=args.strict)
    finally:
        for srv, thr in started:
            stop_ref(srv, thr)

    if rep is None:
        return 2
    rep.print_text()
    if getattr(args, "json_out", None):
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(rep.to_dict(), f, ensure_ascii=False, indent=2)
    return 0 if rep.admitted else 1


if __name__ == "__main__":
    sys.exit(main())
