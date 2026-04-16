# Post Flip-Bug Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy all fixes to the VM, quarantine invalid experiment data, fix state bookkeeping bugs, and update memory notes — cleanup after the engine flip-accounting bug discovered 2026-04-15.

**Architecture:** Deploy local fixes to VM via rsync/scp, update memory files in-place, fix the daily-rollover bug in trader.py that leaves `current_utc_date` stuck, and add deprecation headers to results.tsv files that were produced by the buggy engine.

**Tech Stack:** Python 3.12, SSH to VM (root@100.109.85.37), Letta MCP for memory ops

---

### Task 1: Deploy flip fix + all local changes to VM

**Files:**
- Deploy: `engine/prepare.py` (flip fix)
- Deploy: `paper/trader.py` (flip fix)
- Deploy: `live/trader.py` (maker_pilot revert — already on local, not on VM)
- Deploy: `risk/config.py` (MakerPilotConfig default)

- [ ] **Step 1: Check VM vs local diff**

```bash
ssh root@100.109.85.37 "grep -c 'same_side' /home/openclaw/auto-researchtrading/paper/trader.py /home/openclaw/auto-researchtrading/engine/prepare.py"
```
Expected: 0 matches (VM has the buggy version)

- [ ] **Step 2: Deploy fixes via rsync**

```bash
rsync -avz --include='engine/prepare.py' --include='paper/trader.py' --include='live/trader.py' --include='risk/config.py' --include='risk/' --include='engine/' --include='paper/' --include='live/' --exclude='*' ./ root@100.109.85.37:/home/openclaw/auto-researchtrading/
```

- [ ] **Step 3: Verify deployment**

```bash
ssh root@100.109.85.37 "grep -c 'same_side' /home/openclaw/auto-researchtrading/paper/trader.py /home/openclaw/auto-researchtrading/engine/prepare.py"
```
Expected: 1+ matches per file

- [ ] **Step 4: Verify no crash on maker_pilot**

```bash
ssh root@100.109.85.37 "grep 'maker_cfg = risk_mgr.config.maker_pilot' /home/openclaw/auto-researchtrading/live/trader.py"
```
Expected: Only appears inside a comment or conditional block, not bare execution path. If bare, the trader.py on VM needs the revert from local.

- [ ] **Step 5: Commit**

Already committed as `d2e7742`. No additional commit needed unless VM-specific changes required.

---

### Task 2: Fix state bookkeeping — daily rollover bug in live/trader.py

**Files:**
- Modify: `live/trader.py` — add daily rollover logic in `run_one_tick()`
- Modify: `live/trader.py` — fix `cumulative_fees_total` update for live instances

The `current_utc_date` field is only set in `live/backfill_cb_state.py:234` and never updated in trader.py. There is NO daily rollover logic. When Coinbase resets `daily_realized_pnl` at UTC midnight, the engine's `prior_days_*` fields don't advance.

- [ ] **Step 1: Read the current state update logic**

Read `live/trader.py` lines 185-300 (the `_resync_state_post_orders` function and surrounding bookkeeping) to understand current flow.

- [ ] **Step 2: Add daily rollover at the top of run_one_tick**

At the start of `run_one_tick()`, before any trading logic, detect if the UTC date has advanced and roll over daily fields:

```python
# Daily rollover — advance bookkeeping when UTC date changes
today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
if state.get("current_utc_date") and state["current_utc_date"] != today_utc:
    # Roll yesterday's daily totals into prior-days accumulator
    state["prior_days_realized_pnl"] = (
        (state.get("prior_days_realized_pnl") or 0.0)
        + (state.get("daily_price_pnl") or 0.0)
    )
    state["prior_days_fees"] = (
        (state.get("prior_days_fees") or 0.0)
        + (state.get("daily_fees") or 0.0)
    )
    # Reset daily counters (will be re-populated from Coinbase on this tick)
    state["daily_price_pnl"] = 0.0
    state["daily_fees"] = 0.0
    state["daily_total_pnl"] = 0.0
    logger.info(
        "Daily rollover: %s → %s (prior_days realized=$%.2f fees=$%.2f)",
        state["current_utc_date"], today_utc,
        state["prior_days_realized_pnl"], state["prior_days_fees"],
    )
state["current_utc_date"] = today_utc
```

- [ ] **Step 3: Fix cumulative_fees_total for live instances**

In the live-instance path of `_resync_state_post_orders` (near line 249-265), ensure `cumulative_fees_total` is updated from Coinbase's daily_total_fees:

```python
state["cumulative_fees_total"] = (state.get("prior_days_fees") or 0.0) + daily_fees
```

- [ ] **Step 4: Test by reading current state and verifying the rollover would fire**

```bash
ssh root@100.109.85.37 "python3 -c \"
import json
s = json.load(open('/home/openclaw/auto-researchtrading/live/state/30m-concentrated_live_state.json'))
print('current_utc_date:', s.get('current_utc_date'))
print('Should be 2026-04-16, is:', s.get('current_utc_date'))
\""
```
Expected: Shows `2026-04-12` (the stuck value). After fix + next cron run, should update to today.

- [ ] **Step 5: Commit**

```bash
git add live/trader.py
git commit -m "fix(live): add daily rollover for current_utc_date, prior_days, cumulative_fees

current_utc_date was stuck at 2026-04-12 because no rollover logic existed.
prior_days_realized_pnl and prior_days_fees were always 0.0.
cumulative_fees_total only updated for paper instances, not live.

Now detects UTC date change at top of run_one_tick() and rolls daily
fields into prior-days accumulators before re-populating from Coinbase."
```

---

### Task 3: Quarantine Letta memory notes citing buggy numbers

**Files:**
- Modify: `memory/project_30m_concentrated_phase1_deployed.md`
- Modify: `memory/project_realistic_execution_baseline.md`
- Modify: `memory/project_filter_research_closed.md`
- Modify: `memory/MEMORY.md`

Each memory note that cites backtest/paper returns should be prefixed with a quarantine notice explaining the numbers were produced by a buggy engine.

- [ ] **Step 1: Update project_30m_concentrated_phase1_deployed.md**

Add to the top of the content (after frontmatter):
```
⚠️ QUARANTINED 2026-04-16: Numbers in this note were produced by an engine with a flip-accounting bug (engine/prepare.py, paper/trader.py) that silently hid realized losses on position reversals. See strategies/30m-concentrated/LIVE_RECONCILIATION.md for details. Phase 2 maker pilot and all backtest metrics are unreliable until strategies are re-evaluated on the fixed engine.
```

- [ ] **Step 2: Update project_realistic_execution_baseline.md**

Add same quarantine notice. The +44.67% figure was derived from HL paper which has the same bug.

- [ ] **Step 3: Update project_filter_research_closed.md**

Add quarantine notice. The "edge is regime-invariant" conclusion was based on buggy engine experiments.

- [ ] **Step 4: Update MEMORY.md index**

Add `⚠️` prefix to the three affected entries in the index.

- [ ] **Step 5: No commit needed** — memory files are outside the git repo.

---

### Task 4: Add deprecation header to REALISTIC_EXECUTION_PROJECTION.md

**Files:**
- Modify: `strategies/30m-concentrated/REALISTIC_EXECUTION_PROJECTION.md`

- [ ] **Step 1: Add deprecation header**

Add at top of file:
```markdown
> ⚠️ **DEPRECATED 2026-04-16** — All numbers in this document are derived from HL paper data produced by an engine with a flip-accounting bug that silently hid realized losses on position reversals. The +44.67% "realistic" taker baseline and all maker-pilot projections are unreliable. See `LIVE_RECONCILIATION.md` in this directory for the full post-mortem. Fixed-engine backtest on 9 months of Coinbase data shows the strategy at **−95% return**, not +44%.
```

- [ ] **Step 2: Commit**

```bash
git add strategies/30m-concentrated/REALISTIC_EXECUTION_PROJECTION.md
git commit -m "docs: deprecate REALISTIC_EXECUTION_PROJECTION.md — numbers from buggy engine"
```

---

### Task 5: Add deprecation headers to all results.tsv files

**Files:**
- Modify: All 16 `results.tsv` files across `strategies/*/`

Every results.tsv was produced by the buggy engine. Rather than deleting them (they have historical value as a record of what was tried), add a comment header.

- [ ] **Step 1: Add header to each results.tsv**

For each file, prepend:
```
# ⚠️ DEPRECATED 2026-04-16: Results below produced by engine with flip-accounting bug.
# Sharpe, win rate, return, and profit factor are unreliable for any strategy that
# emits sign-flip signals (most strategies in this repo). Rerun on fixed engine before
# trusting any result. See strategies/30m-concentrated/LIVE_RECONCILIATION.md.
```

TSV readers that skip `#` comment lines will ignore it. Excel/Sheets users will see it.

- [ ] **Step 2: Commit**

```bash
git add strategies/*/results.tsv results.tsv
git commit -m "docs: deprecate all results.tsv — produced by buggy flip-accounting engine"
```

---

### Task 6: Deploy state bookkeeping fix + verify VM health

**Files:**
- Deploy: `live/trader.py` (with Task 2 rollover fix)

- [ ] **Step 1: Deploy updated trader.py to VM**

```bash
scp live/trader.py root@100.109.85.37:/home/openclaw/auto-researchtrading/live/trader.py
```

- [ ] **Step 2: Verify paper instances still run cleanly**

Check the paper-cb cron log for a clean run after deployment:
```bash
ssh root@100.109.85.37 "tail -20 /home/openclaw/auto-researchtrading/live/logs/30m-concentrated-paper-cb-cron.log"
```
Expected: No tracebacks, `Strategy returned N signals` or `no signals — nothing to do`.

- [ ] **Step 3: Verify live cron is still disabled**

```bash
ssh root@100.109.85.37 "crontab -u openclaw -l | grep 'concentrated-live'"
```
Expected: Line is commented out with `# HALTED 2026-04-15` prefix.

---
