"""Cross-team trial supervisor for immutable partner packages.

Run inside an isolated container: no external network, no credentials,
read-only project and archive mounts, bounded CPU/memory/time. Each trial
gets a new extracted copy and a fresh server. Never modify partner sources.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SEEDS = (89725161, 89725187, 89725213)
PEERS = {
    "lab": ("независимые пары/metaroy_lab.zip", "94bcded07e65fc1d9c54813a3d64aa326e0f770e48d8cd455bf8058aa17ace07", "metaroy_lab"),
    "portable": ("независимые пары/metaroy_portable_v0_1.zip", "80076ae142e466758ec2b261a969004281d53048e52b0d6e5dc4d0c13e59cbc9", "metaroy_portable"),
    "atlas": ("независимые пары/Atlas_Probe_0.2.zip", "49c145978273b6b9bd0f0c6a40d7458f7f1749755c6d3a2d5428db0fcf5455a3", "atlas_probe"),
    "v9": ("controller_team2/metaroi-controller-portable-v9.zip", "aaafe5e9887ff6c032f953fa892723ff9ed76f42109194fe57bbe413c5dc0e68", "metaroi-controller"),
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(peer, component, port, work):
    py = [sys.executable, "-B", "-X", "utf8"]
    if peer == "lab":
        if component == "environment":
            return py + ["environment.py", "--host", "127.0.0.1", "--port", str(port),
                         "--log", str(work / "peer-environment.jsonl")]
        return py + ["controller.py", "--serve", "--host", "127.0.0.1", "--port", str(port)]
    if peer == "portable":
        return py + ["-m", "metaroy", component, "--host", "127.0.0.1", "--port", str(port)]
    if peer == "atlas":
        return py + ["controller.py", "serve", "--host", "127.0.0.1", "--port", str(port),
                     "--log", str(work / "peer-controller.jsonl")]
    return ["npm", "run", "start:" + component]


def ready(process, component, ports, deadline):
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("partner startup exit " + str(process.returncode))
        for port in ports:
            url = f"http://127.0.0.1:{port}"
            try:
                with urlopen(url + "/v1/" + component + "/health", timeout=.4) as response:
                    body = json.loads(response.read(65536))
                if isinstance(body, dict) and body.get("component") == component:
                    return url
            except (OSError, ValueError):
                pass
        time.sleep(.15)
    raise TimeoutError("partner health endpoint unavailable")


def stop(process):
    if process is not None:
        # Start each subprocess in a dedicated group; terminate its children too.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=3)


def offline_env(work,port):
    # The controller is launched in OFF mode. Its accounting report must not
    # try to create the Windows production ledger on a read-only Linux mount.
    # This scratch ledger never replaces or resets a real LIVE ledger.
    return dict(os.environ, HOME=str(work), PORT=str(port),
                ENVIRONMENT_PORT=str(port), CONTROLLER_PORT=str(port),
                METARESCUE_OUTPUT=str(work / "metarescue-output"),
                METARESCUE_LIVE_BUDGET=str(work / "offline-ai-budget.json"))


def startup_diagnostic(path):
    # Emit only bounded, recognizable dependency/error codes, never arbitrary
    # partner stdout, private inputs or source snippets into public CI logs.
    if not path.is_file():
        return {}
    with path.open("rb") as stream:
        stream.seek(max(0,path.stat().st_size-32768))
        tail=stream.read(32768).decode("utf-8",errors="replace")
    codes=("ERR_MODULE_NOT_FOUND","MODULE_NOT_FOUND","EADDRINUSE","EACCES","EROFS","ENOSPC",
           "ModuleNotFoundError","ImportError","SyntaxError","TypeError","ReferenceError","ENOENT",
           "ERR_REQUIRE_ESM","ERR_UNKNOWN_FILE_EXTENSION","ERR_UNSUPPORTED_DIR_IMPORT",
           "npm ERR!","vite:","failed to load config","Permission denied","Read-only file system",
           "Cannot find module","not found","Missing script","Unknown option","EEXIST","ENOTDIR",
           "Error:","FATAL ERROR")
    result={"startup_error_codes":[code for code in codes if code in tail]}
    for pattern in (r"Cannot find package ['\"]([a-zA-Z0-9_@./-]{1,100})['\"]",
                    r"No module named ['\"]([a-zA-Z0-9_.-]{1,100})['\"]"):
        match=re.search(pattern,tail)
        if match and not match.group(1).startswith((".","/")):
            result["missing_dependency"]=match.group(1)
            break
    return result


def summary(report):
    result = report.get("run", report)
    if not isinstance(result, dict):
        return {"exchange": "failed", "error": "invalid report"}
    selected = {}
    for key in ("exchange", "status", "goal_status", "goal_verified", "inputs_preserved",
                "planning_p95_ms", "step_http_p95_ms", "exit_code",
                "correlated_public_target_count", "evacuated", "aid_delivered", "located", "journal_unscoped_metadata"):
        value = result.get(key, report.get(key))
        if value is not None and type(value) in (str, int, float, bool):
            selected[key] = value
    metrics = result.get("metrics") or {}
    if isinstance(metrics, dict):
        selected["goal_metrics"] = {k:v for k,v in metrics.items()
            if isinstance(k,str) and any(word in k.lower() for word in ("rescu","evacuat","deliver","target"))
            and type(v) in (int,float,bool)}
    for key in ("confirmed_command_receipts", "confirmed_physical_outcomes", "verified_goals","verified_outcomes"):
        value = result.get(key)
        if isinstance(value, (list, dict)):
            selected[key + "_count"] = len(value)
    error = result.get("first_protocol_error") or report.get("first_protocol_error") or {}
    if isinstance(error,dict):
        selected["first_error"] = {k:str(error[k])[:240] for k in ("kind","code","stage","message","model_time") if k in error}
        details = error.get("details")
        if isinstance(details,dict):
            selected["error_path"] = details.get("path")
            selected["error_details"] = {key:details[key] for key in
                ("rule","entity_type","entity_id","resource_id","declared_id","minimum","value","maximum",
                 "command_index","agent_id","action","scope","evidence_record")
                if key in details and type(details[key]) in (str,int,float,bool)}
            if isinstance(details.get("missing"),list):
                selected["error_details"]["missing"]=[v[:80] for v in details["missing"][:16] if isinstance(v,str)]
    for key in ("api_accounting_error","close_error"):
        if result.get(key):
            selected[key]=str(result[key])[:240]
    truth=result.get("independent_truth")
    if isinstance(truth,dict):
        selected["independent_truth"]={key:truth[key] for key in
            ("profile","target_count","physical_handovers","all_evacuated","closed") if key in truth}
    if result.get("error"):
        selected["error"] = str(result["error"])[:240]
    return selected


def trial(peer, direction, mode, seed, engine='planar'):
    rel, expected, base = PEERS[peer]
    package = ROOT / rel
    row = {"peer":peer,"direction":direction,"mode":mode,"seed":seed,
           "engine":engine if direction=='inbound' else 'partner', "archive_sha256":expected}
    begin = time.monotonic()
    process = runner = None
    with tempfile.TemporaryDirectory(prefix="peer-trial-") as directory:
        work = Path(directory)
        try:
            if digest(package) != expected:
                raise ValueError("archive digest mismatch")
            with zipfile.ZipFile(package) as archive:
                archive.extractall(work)
            cwd = work / base
            if peer=='v9' and Path('/opt/peer-dependencies/node_modules').is_dir():
                # Runtime dependencies installed from the frozen package-lock;
                # no alteration of the partner's archived source.
                (cwd/'node_modules').symlink_to('/opt/peer-dependencies/node_modules',target_is_directory=True)
            component = "environment" if direction == "outbound" else "controller"
            port = 18080
            ports = (port,8000,8001,8080,8081,3000,3001,9000,9001,8130,8131) if peer == "v9" else (port,)
            env = offline_env(work,port)
            with (work / "peer.log").open("wb") as plog, (work / "runner.log").open("wb") as rlog:
                process = subprocess.Popen(command(peer,component,port,work), cwd=cwd,
                                           env=env, stdout=plog, stderr=subprocess.STDOUT, start_new_session=True)
                url = ready(process,component,ports,time.monotonic()+12)
                row["health_reached"] = True
                args = [sys.executable,"-B","-X","utf8","tools/run_interop.py",
                        "our-controller" if direction=="outbound" else "our-environment",
                        "--seed",str(seed),"--timeout","35","--core-mode",mode,
                        "--input-package",str(package),"--output",str(work / "report")]
                if direction == "outbound":
                    args += ["--environment",url]
                else:
                    endpoint = "decision" if peer in ("lab","atlas") else "decide"
                    args += ["--controller",url,"--mode","decide","--engine",engine,
                             "--environment-port","18440","--decide-path",endpoint,
                             "--decision-timeout","7"]
                    if peer=="v9":
                        args += ["--decide-profile","metaroi-v9"]
                runner = subprocess.Popen(args,cwd=ROOT,env=env,stdout=rlog,stderr=subprocess.STDOUT,
                                          start_new_session=True)
                row["runner_exit"] = runner.wait(timeout=50)
                report = work / "report" / "report.json"
                if not report.is_file():
                    raise RuntimeError("trial runner did not produce report")
                row.update(summary(json.loads(report.read_bytes())))
        except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as error:
            row.update(exchange="failed",error=str(error)[:240])
        finally:
            stop(runner)
            stop(process)
            if not row.get("health_reached"):
                row.update(startup_diagnostic(work/"peer.log"))
            row["archive_preserved"] = digest(package)==expected
            row["wall_s"] = round(time.monotonic()-begin,3)
    print(json.dumps(row,ensure_ascii=False),flush=True)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("peer",choices=PEERS)
    parser.add_argument("direction",choices=("outbound","inbound"))
    parser.add_argument("--seed",type=int,action="append")
    parser.add_argument("--mode",choices=("strict","declared"),default="strict")
    parser.add_argument("--engine",choices=("planar","reference"),default="planar")
    options = parser.parse_args()
    if options.peer=="atlas" and options.direction=="outbound":
        parser.error("Atlas archive contains no standalone environment")
    rows = [trial(options.peer,options.direction,options.mode,seed,options.engine) for seed in options.seed or SEEDS]
    print(json.dumps({"completed_trials":len(rows),"passed_exchanges":sum(r.get("exchange")=="passed" for r in rows),
                      "all_inputs_preserved":all(r["archive_preserved"] for r in rows)},ensure_ascii=False),flush=True)
    # This is a measurement job, not a normative acceptance gate. Negative
    # interoperability findings are deliberately preserved, not hidden.
    return 0 if all(r["archive_preserved"] for r in rows) else 1


if __name__=="__main__":
    raise SystemExit(main())
