"""Correlate declared mission outcomes with commands and physical evidence."""
from copy import deepcopy
import hashlib
import json

from contracts.action_semantics import declared_goal_effects


def decision_fingerprint(decision):
    """Compare decoded records canonically; exact wire bytes are audited separately."""
    try:
        raw=json.dumps(decision,sort_keys=True,ensure_ascii=False,allow_nan=False,
                       separators=(',',':')).encode('utf-8')
    except (TypeError,ValueError,UnicodeError,RecursionError):
        return None
    return hashlib.sha256(raw).digest()


def terminal_failure(receipt):
    return (receipt.get('validation_status')=='rejected'
            or receipt.get('delivery_status') in ('lost','expired')
            or receipt.get('execution_status') in ('failed','cancelled'))


class GoalEvidence:
    def __init__(self, capabilities):
        self.effects = declared_goal_effects(capabilities)
        self.target_parameters = {}
        for wire in self.effects:
            description = capabilities['action_semantics'][wire]
            bindings = description.get('bindings', {})
            name = bindings.get('target_id', 'target_id') if isinstance(bindings, dict) else None
            self.target_parameters[wire] = name if isinstance(name, str) else None
        self.pending = {}
        self.outcomes = {}
        self.receipts = {}
        self.conflicting_decisions = set()
        self.conflicting_outcomes = set()
        self.failed_receipts = set()
        self.decisions = {}
        self.requests = {}

    @staticmethod
    def key(episode, item):
        if not isinstance(item,dict) or item.get('episode_id',episode)!=episode:
            return None
        decision, index = item.get('decision_id'), item.get('command_index')
        return (episode, decision, index) if (isinstance(episode, str) and episode
                and isinstance(decision, str) and decision and type(index) is int and index >= 0) else None

    def record_decision(self, decision):
        if not isinstance(decision,dict):
            return
        episode, identifier = decision.get('episode_id'), decision.get('decision_id')
        commands = decision.get('commands', ())
        if not isinstance(episode, str) or not episode or not isinstance(identifier, str) or not identifier:
            return
        if not isinstance(commands, (list, tuple)):
            return
        fingerprint=decision_fingerprint(decision)
        if fingerprint is None:
            return
        signatures = {}
        for index, cmd in enumerate(commands):
            if not isinstance(cmd, dict) or not isinstance(cmd.get('action'), str) or cmd['action'] not in self.effects:
                continue
            params = cmd.get('parameters', {})
            if not isinstance(params, dict) or not isinstance(cmd.get('agent_id'), str):
                continue
            name = self.target_parameters[cmd['action']]
            target = params.get(name) if name is not None else None
            signatures[index] = (cmd['agent_id'], cmd['action'], target)
        decision_key = (episode, identifier)
        request=decision.get('request_id')
        if isinstance(request,str) and request:
            request_key=(episode,request)
            prior=self.requests.setdefault(request_key,decision_key)
            if prior!=decision_key:
                self.requests[request_key]=None
                self.conflicting_decisions.add(decision_key)
                if prior is not None:
                    self.conflicting_decisions.add(prior)
        if decision_key in self.decisions and self.decisions[decision_key] != fingerprint:
            self.conflicting_decisions.add(decision_key)
            return
        self.decisions[decision_key] = fingerprint
        for index, signature in signatures.items():
            self.pending.setdefault((*decision_key, index), deepcopy(signature))

    def record_receipt(self, episode, item):
        key = self.key(episode, item)
        if key and terminal_failure(item):
            self.failed_receipts.add(key)
        # Keep terminal successful receipts even if logs arrive before the
        # corresponding decision. The full correlation is checked at readout.
        if (key and item.get('validation_status') == 'accepted'
                and item.get('delivery_status') == 'delivered'
                and item.get('execution_status') == 'completed'
                and isinstance(item.get('agent_id'), str) and isinstance(item.get('action'), str)):
            signature = (item['agent_id'], item['action'])
            self.receipts.setdefault(key, set()).add(signature)

    def record_outcome(self, episode, occurrence):
        key = self.key(episode, occurrence)
        if key is None:
            return
        effect, ident, target = (occurrence.get(field) for field in ('effect', 'outcome_id', 'target_id'))
        if (not key or occurrence.get('episode_id',episode)!=episode
                or not isinstance(ident, str) or not ident or not isinstance(target, str) or not target
                or effect not in ('evacuated', 'located', 'aid_delivered')):
            return
        outcome_key = (episode, ident)
        signature = (key, occurrence.get('agent_id'), effect, target)
        old = self.outcomes.get(outcome_key)
        if old is not None and old[0] != signature:
            self.conflicting_outcomes.add(outcome_key)
        else:
            self.outcomes.setdefault(outcome_key, (signature, deepcopy(occurrence)))

    def verified(self):
        results = []
        seen = set()
        for outcome_key, (signature, occurrence) in self.outcomes.items():
            key, agent, effect, target = signature
            expected = self.pending.get(key)
            if (outcome_key in self.conflicting_outcomes or key[:2] in self.conflicting_decisions
                    or key in self.failed_receipts or not expected):
                continue
            expected_agent, action, expected_target = expected
            if (agent != expected_agent or self.receipts.get(key) != {(expected_agent, action)}
                    or self.effects.get(action) != effect
                    or (expected_target is not None and target != expected_target)):
                continue
            # Re-observation of the same target or replay of a transfer is not
            # another completed target. Multiple distinct targets may be
            # located by one scan; separate aid commands may deliver again.
            target_key = (key[0], effect, target) if effect != 'aid_delivered' else (key, effect, target)
            if target_key not in seen:
                seen.add(target_key)
                results.append({**deepcopy(occurrence),'episode_id':key[0]})
        return results

    def summary(self):
        results = self.verified()
        return {'goal_status': 'verified' if results else 'unverified',
                'verified_outcomes': results,
                'evacuated': sum(item['effect'] == 'evacuated' for item in results),
                'aid_delivered': sum(item['effect'] == 'aid_delivered' for item in results),
                'located': sum(item['effect'] == 'located' for item in results)}
