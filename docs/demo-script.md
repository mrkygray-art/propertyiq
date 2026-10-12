# Demo script

Steps marked *(not built yet)* depend on later milestones.

1. **Open the dashboard** at http://127.0.0.1:3000 and sign in as `analyst`. The banner reads **Synthetic data · Private local model · Not investment advice**, and the header shows the database and local model as ready.
2. **Ask "Summarize our underperforming assets."** Expect **4 of 55** flagged and a **$420,000** total budget shortfall. The answer is labeled either "Local model · figures verified" or "Verified summary", and lists its sources and the facts sent to the model.
3. **Click Westlake (PIQ-001).** Check 72% occupancy, −18% NOI YoY and a $180,000 shortfall. All three rules are triggered. The page shows the 36-month NOI-vs-budget and occupancy charts, plus the calculation trail with inputs, formulas and source record IDs.
4. **Ask "Why was Westlake flagged?"** The answer states the known facts and explicitly says there is no causal evidence in the data yet. Then ask "Show the underlying NOI budget variance." It understands that the follow-up is still about Westlake.
5. **Add the synthetic lease/expense report** and ask again. The answer should now cite the document's title and page. *(Milestone 2)*
6. **Sign in as `restricted`.** It sees 27 properties and 2 flags (Harbor and Sunset, $120,000). Visiting `/properties/PIQ-001` shows "Property not found, or you don't have access to it." Asking the chat about Westlake gets "I couldn't find a property by that name among the properties you have access to."
7. **Block outbound internet** with firewall rules and repeat steps 2–4. Show the firewall log and connection list. *(Milestone 3)*
8. **Show the tests and documents:** `pytest`, [architecture.md](architecture.md), [security-boundary.md](security-boundary.md), and what remains for production.

## Run it

```powershell
powershell -ExecutionPolicy Bypass -File C:\Users\mrkyg\projects\propertyIQ\scripts\start.ps1
```

**Before a live demo:** ask one question first to load the model, which takes about 30 seconds the first time. After that, answers take about 7–15 seconds.
