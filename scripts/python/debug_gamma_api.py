from agents.polymarket.polymarket import Polymarket
p = Polymarket()
mkts = p.get_markets_closing_soon()
print(f"\nTOTAL: {len(mkts)} mercados")

# NBA
nba = [m for m in mkts if any(k in m.get("question","").lower() for k in ["pistons","lakers","thunder","cavaliers","timberwolves","spurs"])]
print(f"\nNBA: {len(nba)} mercados")
for m in nba[:10]:
    print(f"  {m.get('question','?')[:70]}")

# Soccer
soccer_kw = ["rayo", "girona", "tottenham", "leeds", "barcelona", "madrid"]
soccer = [m for m in mkts if any(k in m.get("question","").lower() for k in soccer_kw)]
print(f"\nSoccer (times conhecidos): {len(soccer)} mercados")
for m in soccer[:10]:
    print(f"  {m.get('question','?')[:70]}")
