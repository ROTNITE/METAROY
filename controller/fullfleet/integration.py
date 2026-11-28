"""Production entry point for the opt-in portable FullFleet profile."""
from controller.fullfleet.bounds import supported_bounds
from controller.fullfleet.fast_full_fleet import FullFleetFastPolicy


class BoundedFullFleetPolicy(FullFleetFastPolicy):
    def _research_constraints_supported(self, observation):
        return self._profile() and supported_bounds(observation)

    def _arrival_constraints_supported(self, observation, agent=None):
        return self._research_constraints_supported(observation)


def make_portable(*args, **kwargs):
    return BoundedFullFleetPolicy(*args, rollout_depth=2, per_rescue=True, all_routes=True,
                                  track_speed=True, service_estimate=True, tick_service=True, **kwargs)
