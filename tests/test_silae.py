from datetime import date, time

import pytest

from planning_relay.connecteurs.silae import ConnecteurSilae, creneaux_de

MOI, AUTRE = "1000000001", "1000000002"


def ev(code, start, end, all_day=False, owner="employee", employee=MOI, **kw):
    return {"code": code, "type": "ABSENCE" if all_day else "WORK", "start": start, "end": end[:10] + " 23:59+02:00" if all_day else end,
            "_end": end, "isAllDay": all_day, "owner": owner, "employee": employee, **kw}


# Forme relevée le 5 octobre 2026 sur /planning/json/employee/events
EVENEMENTS = [
    ev("SOIR", "2026-10-05 14:45+02:00", "2026-10-05 22:45+02:00", label="RECEP SOIR", durationText="7h30", breakTime=30),
    {"code": "S", "type": "service", "owner": "service", "start": "2026-10-06 00:00+02:00", "end": "2026-10-06 00:00+02:00",
     "isAllDay": 1, "description": "Zakia matin"},
    ev("NIGHT", "2026-10-06 22:45+02:00", "2026-10-07 07:00+02:00", label="Semaine", durationText="7h45", breakTime=30),
    ev("RF", "2026-10-09 00:00+02:00", "2026-10-09 23:59+02:00", all_day=True, label="Récup Férié", durationText="0h", breakTime=0),
    ev("07H", "2026-10-11 07:00+02:00", "2026-10-11 15:45+02:00", employee=AUTRE),
]


def test_seuls_les_creneaux_du_matricule_sortent():
    cs = creneaux_de(EVENEMENTS, MOI)
    assert [c.code for c in cs] == ["SOIR", "NIGHT", "RF"]


def test_night_utilise_la_vraie_fin():
    night = creneaux_de(EVENEMENTS, MOI)[1]
    assert (night.jour, night.debut, night.fin) == (date(2026, 10, 6), time(22, 45), time(7, 0))
    assert night.fin_le_lendemain and night.duree == "7h45" and night.pause == "30 min"


def test_absence_journee_entiere_sans_duree():
    rf = creneaux_de(EVENEMENTS, MOI)[2]
    assert rf.journee_entiere and rf.duree is None and rf.pause is None and rf.intitule == "Récup Férié"


def test_matricule_obligatoire():
    with pytest.raises(ValueError):
        ConnecteurSilae("u", "p", "Charlène")
