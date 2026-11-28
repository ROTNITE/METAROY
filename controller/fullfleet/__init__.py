"""FullFleet Fast portable research implementation; no environment imports."""

def make_policy(*args, **kwargs):
    from controller.fullfleet.fast_full_fleet import FullFleetFastPolicy
    return FullFleetFastPolicy(*args, rollout_depth=2, per_rescue=True, all_routes=True,
                               track_speed=True, service_estimate=True, tick_service=True, **kwargs)
