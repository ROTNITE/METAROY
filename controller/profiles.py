"""Controller selection shared by native, portable and product entry points."""

STANDARD = 'standard'
COUNTERFACTUAL = 'counterfactual-value'
FULLFLEET = 'cf-full-fleet-fast'
COUNTERFACTUAL_PROFILES = (COUNTERFACTUAL, FULLFLEET)
CONTROLLER_PROFILES = (STANDARD, *COUNTERFACTUAL_PROFILES)


def select_patrol(profile, policy, patrol):
    if profile not in CONTROLLER_PROFILES:
        raise ValueError('Unknown controller profile')
    if profile in COUNTERFACTUAL_PROFILES:
        if policy != 'nsga2_nsga2' or patrol not in (None, 'net65'):
            raise ValueError('Counterfactual profiles require NSGA-II x2 and the Net65 patrol')
        return 'net65'
    return 'core' if patrol is None else patrol
