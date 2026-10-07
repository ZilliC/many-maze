"""Example procedures (offered by the procedure editor)."""

EXAMPLES = {
    "Fear conditioning (tone + shock)": {"name": "Fear conditioning", "enabled": True, "statements": [
        {"type": "comment", "text": "2 min baseline, then 3 tone–shock pairings with random intervals"},
        {"type": "var", "name": "pairings", "value": 0, "result": True},
        {"type": "wait", "mode": "seconds", "seconds": 120},
        {"type": "repeat", "mode": "count", "count": 3, "body": [
            {"type": "do", "action": "tone", "frequency": 2800, "duration": 30, "volume": 0.8},
            {"type": "do", "action": "mark_start", "name": "Tone"},
            {"type": "wait", "mode": "seconds", "seconds": 28},
            {"type": "do", "action": "shock_pulse", "device": "", "channel": "shocker", "duration": 2},
            {"type": "wait", "mode": "seconds", "seconds": 2},
            {"type": "do", "action": "mark_end", "name": "Tone"},
            {"type": "set", "var": "pairings", "value": "pairings + 1"},
            {"type": "wait", "mode": "seconds", "seconds": "randint(60, 120)"}]}]},
    "Lever press for food (FR 5)": {"name": "FR5 lever", "enabled": True, "statements": [
        {"type": "var", "name": "rewards", "value": 0, "result": True},
        {"type": "when", "event": "test_start", "body": [
            {"type": "do", "action": "schedule_start", "schedule": "lever", "spec": "FR 5"},
            {"type": "do", "action": "light_on", "device": "", "channel": "house_light"}]},
        {"type": "when", "event": "input_on", "device": "", "channel": "lever", "mode": "parallel", "body": [
            {"type": "do", "action": "schedule_response", "schedule": "lever", "spec": "", "var": "due"},
            {"type": "if", "cond": "due", "body": [
                {"type": "do", "action": "pellet", "device": "", "channel": "pellet", "count": 1},
                {"type": "do", "action": "increment", "var": "rewards", "by": 1}]}]},
        {"type": "when", "event": "variable_changed", "var": "rewards", "body": [
            {"type": "if", "cond": "rewards >= 50", "body": [{"type": "stop", "what": "test"}]}]}]},
    "Optogenetic stimulation in a zone": {"name": "Opto in zone", "enabled": True, "statements": [
        {"type": "when", "event": "zone_enter", "zone": "", "mode": "restart", "body": [
            {"type": "if", "cond": "event_name == 'Centre'", "body": [
                {"type": "do", "action": "pulse_train", "device": "", "channel": "laser", "frequency": 20,
                 "pulse_width": 5, "duration": 0}]}]},
        {"type": "when", "event": "zone_exit", "zone": "", "body": [
            {"type": "if", "cond": "event_name == 'Centre'", "body": [
                {"type": "do", "action": "pulse_train_stop", "device": "", "channel": "laser"}]}]}]},
    "Spontaneous alternation counter": {"name": "Alternations", "enabled": True, "statements": [
        {"type": "var", "name": "arms", "value": "[]"},
        {"type": "var", "name": "alternations", "value": 0, "result": True},
        {"type": "when", "event": "zone_enter", "zone": "", "mode": "parallel", "body": [
            {"type": "if", "cond": "event_name in ['A', 'B', 'C']", "body": [
                {"type": "do", "action": "array_append", "var": "arms", "value": "event_name"},
                {"type": "if", "cond": "len(arms) >= 3 and arms[-1] != arms[-2] and arms[-2] != arms[-3] "
                                       "and arms[-1] != arms[-3]",
                 "body": [{"type": "do", "action": "increment", "var": "alternations", "by": 1}]}]}]}]},
}

