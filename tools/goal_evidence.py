"""Correlate declared mission outcomes with commands and physical evidence."""
from contracts.action_semantics import declared_goal_effects


class GoalEvidence:
    def __init__(self, capabilities):
        self.effects=declared_goal_effects(capabilities)
        self.pending={}
        self.outcomes={}
        self.receipts={}

    @staticmethod
    def key(episode, item):
        decision,index=item.get('decision_id'),item.get('command_index')
        return (episode,decision,index) if isinstance(episode,str) and isinstance(decision,str) and type(index) is int and index>=0 else None

    def record_decision(self, decision):
        episode=decision.get('episode_id')
        if not isinstance(episode,str) or not isinstance(decision.get('decision_id'),str):
            return
        if any(self.effects.get(row.get('action')) for row in decision.get('commands',()) if isinstance(row,dict)):
            for index,cmd in enumerate(decision['commands']):
                if isinstance(cmd,dict) and cmd.get('action') in self.effects:
                    key=(episode,decision['decision_id'],index)
                    signature=(cmd.get('agent_id'),cmd['action'])
                    self.pending.setdefault(key,signature)

    def record_receipt(self, episode, item):
        key=self.key(episode,item)
        expected=self.pending.get(key)
        if (expected and expected==(item.get('agent_id'),item.get('action')) and
                item.get('validation_status')=='accepted' and item.get('delivery_status')=='delivered'
                and item.get('execution_status')=='completed'):
            self.receipts.setdefault(key,item)

    def record_outcome(self, episode, occurrence):
        key=self.key(episode,occurrence)
        if not key:
            return
        effect=occurrence.get('effect')
        ident=occurrence.get('outcome_id')
        if not isinstance(ident,str) or not isinstance(occurrence.get('target_id'),str) or effect not in ('evacuated','located','aid_delivered'):
            return
        known=self.outcomes.get((episode,ident))
        if not known:
            self.outcomes[(episode,ident)]=(key,occurrence)

    def verified(self):
        results=[]
        seen=set()
        for key,occurrence in self.outcomes.values():
            receipt=self.receipts.get(key)
            target_key=(key[0],occurrence['effect'],occurrence['target_id'] if occurrence['effect']=='evacuated' else key)
            if (receipt and receipt.get('agent_id')==occurrence.get('agent_id') and
                    self.effects.get(receipt.get('action'))==occurrence['effect'] and target_key not in seen):
                seen.add(target_key)
                results.append(occurrence)
        return results

    def summary(self):
        results=self.verified()
        return {'goal_status':'verified' if results else 'unverified',
                'verified_outcomes':results,
                'evacuated':sum(item['effect']=='evacuated' for item in results),
                'aid_delivered':sum(item['effect']=='aid_delivered' for item in results),
                'located':sum(item['effect']=='located' for item in results)}
