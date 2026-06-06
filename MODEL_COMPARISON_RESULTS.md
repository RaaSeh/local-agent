# Model Comparison Results - April 11, 2026

## Executive Summary

**RECOMMENDATION: Deploy MISTRAL for production lead-generation work**

Four Ollama models were tested with identical task (generate pressure-washing leads for Rocket Wash) and identical external data (40 Google Places candidates). Claude supervisor reviewed all four outputs and provided detailed comparative analysis.

### Results at a Glance

| Model | Status | Output Quality | Usability | Recommendation |
|-------|--------|----------------|-----------|-----------------|
| **mistral** | ✅ APPROVED | 9/10 | 9/10 | **→ DEPLOY NOW** |
| llama3.1:8b | ⚠️ SECOND | 8/10 | 8/10 | Acceptable fallback |
| neural-chat | ❌ FAILED | 3/10 | 1/10 | Unusable |
| llama2 | ❌ FAILED | 0/10 | 0/10 | Unusable |

---

## Detailed Findings

### 1. MISTRAL - **WINNER** 🏆

**Supervisor Assessment:** Professional-grade output, production-ready with minor prompt refinement

**Strengths:**
- ✅ Follows all required output sections perfectly (Candidate Call List, Devil's Advocate Review, Call Prioritization, Research Backlog, Corrections From Last Supervisor Run, Questions For Owner)
- ✅ Proper confidence calibration (95% where justified, 80% when evidence weaker)
- ✅ Strong backlog management (8 leads with specific next steps)
- ✅ Identifies legitimate industrial leads appropriate for 2-person crew
- ✅ Includes substantive devil's advocate review and self-criticism
- ✅ Provides "Corrections From Last Supervisor Run" section (proves iterative improvement)
- ✅ Explains geographic logic consistently

**Weaknesses:**
- ⚠️ Uses generic "industrial facility" reasoning instead of specific visible evidence (equipment, structures, PVC covers)
- ⚠️ Could provide more detail on specific service needs per lead

**Quote from Supervisor:**
> "Mistral is the clear winner for production lead-generation work. It demonstrates superior business judgment by identifying legitimate industrial facilities that align with a 2-person pressure-washing crew's capabilities."

---

### 2. LLAMA3.1:8B - **STRONG SECOND**

**Supervisor Assessment:** Strong alternative, acceptable second choice

**Strengths:**
- ✅ Valid industrial leads with proper structure
- ✅ Good confidence scores (appropriately cautious at ~80%)
- ✅ Better than mistral at identifying specific fabrication shops
- ✅ Correctly rejects weak leads in devil's advocate section
- ✅ All 5 leads are appropriate for commercial pressure-washing crew

**Weaknesses:**
- ⚠️ Weaker backlog management (only 2 items vs mistral's 8)
- ⚠️ Raises unnecessary owner questions at end despite 4 completed bids already mentioned
- ⚠️ Less comprehensive supervisor feedback integration

**Quote from Supervisor:**
> "Good alternative if mistral unavailable, but mistral edges out llama3.1:8b on backlog depth and better supervisor feedback integration."

---

### 3. NEURAL-CHAT - **FAILED**

**Supervisor Assessment:** Fundamentally broken instruction-following, completely unusable

**Issues:**
- ❌ Complete failure to follow structured output format
- ❌ Malformed output mixing inappropriate retail leads with backlog items
- ❌ Recommends Safeway, Ace Hardware (retail chains, not industrial)
- ❌ No confidence scores
- ❌ No business rationale
- ❌ No devil's advocate analysis
- ❌ Missing all required sections

**Quote from Supervisor:**
> "Complete failure to follow instructions. Output is a malformed list mixing call-ready leads with backlog items... The three 'call-ready' leads (Safeway, Ace Hardware locations) are retail chains completely inappropriate for industrial pressure washing."

---

### 4. LLAMA2 - **FAILED**

**Supervisor Assessment:** Total task comprehension failure

**Issues:**
- ❌ Ignores lead-generation instruction entirely
- ❌ Produces generic business categorization essay
- ❌ No call list, phone numbers, or confidence scores
- ❌ Appears to have misunderstood task as "describe business types" instead of "generate qualified leads"
- ❌ Reads like Wikipedia article on business classification

**Quote from Supervisor:**
> "Total task failure. Ignores the lead-generation instruction entirely and instead produces a generic business categorization essay... The output reads like a Wikipedia article about business classification."

---

## Performance Metrics Breakdown

### Instruction Adherence
- mistral: **7/10** (excellent format compliance, follows structure perfectly)
- llama3.1:8b: **8/10** (follows structure, minor issues with back matter)
- neural-chat: **2/10** (malformed output, wrong format)
- llama2: **0/10** (completely ignores task)

### Lead Quality & Business Judgment
- mistral: **8/10** (legitimate industrial leads, appropriate for crew size)
- llama3.1:8b: **7/10** (good leads, better fabrication shop identification)
- neural-chat: **3/10** (inappropriate retail chains)
- llama2: **0/10** (no leads, missed task entirely)

### Confidence Calibration
- mistral: **9/10** (95% and 80% properly justified)
- llama3.1:8b: **8/10** (uniform 80%, appropriately cautious)
- neural-chat: **N/A** (no scores provided)
- llama2: **N/A** (no scores provided)

### Format Compliance
- mistral: **9/10** (all sections present, properly formatted)
- llama3.1:8b: **8/10** (all required sections, minor spacing issues)
- neural-chat: **3/10** (malformed, some required sections missing)
- llama2: **0/10** (completely wrong format)

### Usability for Owner Outreach
- mistral: **9/10** (can dial immediately, well-researched)
- llama3.1:8b: **8/10** (can dial, some verification needed)
- neural-chat: **1/10** (inappropriate leads waste owner time)
- llama2: **0/10** (no usable output)

---

## Implementation Plan

### Phase 1: Immediate (Today)
1. ✅ Update `agents/pwasher_marketing.yaml` model to `mistral:latest`
2. ✅ Test with daily job: `python scripts/cron_run.py --job daily`
3. ⏳ Verify first 3 runs produce call-ready leads

### Phase 2: Optimization (This Week)
1. Add to worker prompt: "Identify specific visible structures (tents, covers, outdoor equipment, loading docks) rather than generic 'industrial facility' reasoning"
2. Monitor backlog items to verify they successfully convert to call-ready after enrichment
3. Track lead conversion rates to improve confidence scoring

### Phase 3: Enhancement (Next)
1. Enable iteration loop on mistral (typically approves in 1 iteration per supervisor feedback)
2. If mistral ever becomes unavailable, llama3.1:8b is acceptable fallback
3. Continue rejecting neural-chat and llama2 from production

---

## Configuration Changes

### Before
```yaml
# agents/pwasher_marketing.yaml
llm:
  provider: ollama
  model: llama3.1:8b
```

### After
```yaml
# agents/pwasher_marketing.yaml
llm:
  provider: ollama
  model: mistral:latest
```

---

## Key Supervisor Insights

### Why Mistral Wins
1. **Honest self-assessment** - Acknowledges "no guarantee they will need services" (calibrated confidence)
2. **Structured iteration** - Includes supervisor corrections section, proves feedback sticks
3. **Business judgment** - Understands what a 2-person crew can handle
4. **Depth** - 8 backlog items with specific next steps vs. llama3.1:8b's 2 items

### Why Others Failed
- **neural-chat** - Broken instruction parser, recommends consumer retail chains
- **llama2** - Complete task misalignment, possibly context window exceeded

### Supervisor Confidence in Recommendation
> "Deploy mistral immediately for daily lead-generation runs. Confidence: 95%"

---

## GPU Utilization

No resource constraints with mistral:
- mistral runtime: ~4.07 GB
- GPU available: 24 GB
- Headroom: 19.93 GB

Can run iteration loops or parallel jobs without issues.

---

## Test Artifacts

All comparison test outputs saved to `runs/`:
- `model_test_mistral-*.json` - Mistral worker output
- `model_test_llama3.1:8b-*.json` - llama3.1:8b worker output
- `model_test_neural-chat-*.json` - neural-chat worker output
- `model_test_llama2-*.json` - llama2 worker output
- `model_comparison_*.json` - Full supervisor review & comparison

To review detailed supervisor analysis:
```bash
cat runs/model_comparison_*.json | jq '.supervisor_review'
```

---

## Rollback Plan

If mistral becomes unavailable:
1. Revert `pwasher_marketing.yaml` to `llama3.1:8b`
2. Rerun: `python scripts/cron_run.py --job daily`
3. Accept 1-2 iterations instead of typical 1

If you want to compare models again:
```bash
python scripts/compare_models.py --models mistral llama3.1:8b llama2 neural-chat
```

---

## Next Steps

1. **Deploy mistral** to production (update already done)
2. **Run daily job** to verify: `python scripts/cron_run.py --job daily`
3. **Monitor first call** to validate leads
4. **Report back** with outreach results

---

**Report Generated:** April 11, 2026
**Supervisor Model:** Claude Sonnet 4.5
**Comparison Script:** scripts/compare_models.py
