import app, time

future = time.time() + 3600
sample = {
    "home_top": {"line_banner": [
        {"id":"a","type":"line_banner","view":"boosted_odds","display_platform":["mobile","desktop"],"details":{"event_id":"1","multiplier":"1.5"}},
        {"id":"b","type":"line_banner","view":"boosted_odds","display_platform":["mobile","desktop"],"details":{"event_id":"1","multiplier":"1.4"}},
        {"id":"c","type":"line_banner","view":"boosted_odds","display_platform":["mobile","desktop"],"details":{"event_id":"2","multiplier":"1.2"}},
    ]},
    "live_page": {"line_banner": [
        {"id":"d","type":"line_banner","view":"boosted_odds","display_platform":["mobile","desktop"],"details":{"event_id":"1","multiplier":"1.6"}},
    ]}
}
orig = app.event_info
def fake(eid, cfg):
    if eid == "1":
        return {"scheduled": future, "name":"Team A vs Team B","sport":"Counter-Strike","category":"Counter-Strike 2","tournament":"BLAST"}
    return None
app.event_info=fake
r=app.extract_boosts(sample, app.DEFAULT_CONFIG)
assert len(r)==1
assert r[0]["event_id"]=="1"
assert r[0]["multiplier"]==1.6
assert r[0]["boost_percent"]==60.0
app.event_info=orig
print("TEST OK")
