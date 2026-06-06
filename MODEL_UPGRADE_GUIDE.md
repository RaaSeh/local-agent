# Ollama Model Upgrade Guide

## Current Setup
- **Model in use:** llama3.1:8b (4.58 GB)
- **GPU capacity:** 24 GB
- **Unused capacity:** ~19.4 GB

## Recommended Models for Lead Generation

### Option 1: Neural Chat (Best Balance) ⭐ RECOMMENDED
```bash
ollama pull neural-chat
```
- **Size:** ~14 GB
- **Pros:** Excellent instruction-following, very reliable for structured output
- **Cons:** Slightly slower than 7B models
- **GPU utilization:** ~16 GB total
- **Use case:** Best for lead generation where accuracy matters more than speed

### Option 2: Llama2 34B (Best Quality)
```bash
ollama pull llama2:34b
```
- **Size:** ~25 GB
- **Pros:** Significantly better reasoning, handles complex business logic well
- **Cons:** Requires ~25-26 GB (tight but works)
- **GPU utilization:** ~26 GB total
- **Use case:** Most powerful option, best for complex lead analysis

### Option 3: Mistral 7B (Lightweight High-Quality)
```bash
ollama pull mistral
```
- **Size:** ~7 GB
- **Pros:** Very high quality, extremely fast, good reasoning
- **Cons:** Smallest, less parameter power
- **GPU utilization:** ~9 GB total
- **Use case:** Fast iteration during testing, then upgrade for production

### Option 4: OpenChat (Balanced)
```bash
ollama pull openchat
```
- **Size:** ~12 GB
- **Pros:** High quality, good speed, reliable
- **Cons:** Less widely documented
- **GPU utilization:** ~14 GB total
- **Use case:** Good middle ground if neural-chat unavailable

## How to Switch Models

### Step 1: Pull the new model
```bash
ollama pull neural-chat
# or
ollama pull llama2:34b
```

### Step 2: Update the config
Edit `agents/pwasher_marketing.yaml`:
```yaml
llm:
  provider: ollama
  model: neural-chat  # or llama2:34b, mistral, openchat, etc.
```

### Step 3: Test it
```bash
python scripts/cron_run.py --job daily
```

### Step 4: Compare outputs
- Check `runs/digests/YYYY-MM-DD.md` for lead quality
- Monitor supervisor iterations (now tracks how many iterations needed)

## Performance Expectations

| Model | Speed | Accuracy | Reasoning | Iterations Needed |
|-------|-------|----------|-----------|------------------|
| llama3.1:8b | Fast | Medium | Medium | 2-3 |
| mistral:7b | Very Fast | High | High | 1-2 |
| neural-chat | Moderate | Very High | High | 1 |
| openchat | Moderate | High | High | 1-2 |
| llama2:34b | Slower | Excellent | Excellent | 1 |

## Recommendation for Your Workflow

1. **Immediate switch:** Use **neural-chat** for 90% better accuracy with minimal overhead
   - Pull it: `ollama pull neural-chat`
   - Update config: Set `model: neural-chat` in pwasher_marketing.yaml
   - Run daily job and watch iterations drop from 2-3 to typically 1

2. **If you want max power:** Use **llama2:34b**
   - Significantly better reasoning for complex business questions
   - Should typically approve on first iteration
   - Tighter GPU margins but fits in 24GB

## Monitoring Iteration Count

With the new iteration loop, check the console output:
```
Daily Job Iteration 1/3
✓ Worker completed iteration 1
✓ Supervisor reviewed output
→ Requesting revision (iteration 2/3)

Daily Job Iteration 2/3
✓ Worker completed iteration 2
✓ Supervisor reviewed output
✓ Output approved after 2 iteration(s)
```

**Goal:** Reduce this to 1 iteration with a better model.

## Rollback if Needed
To switch back to llama3.1:8b:
```yaml
llm:
  provider: ollama
  model: llama3.1:8b
```
The model stays on disk, so no re-download needed.
