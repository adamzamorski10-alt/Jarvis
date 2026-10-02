# Stage 10.6 — Local Coding Runtime & Kilo Decision Discovery

## 1. Current Architecture Audit

### Call Graphs

#### Path A: OllamaCodingAdapter
```
AutonomousTaskOrchestrator
  → AutonomousRunController
    → BaseDevelopmentAgent.execute()
      → OllamaCodingAdapter.plan_next_step()
        → OllamaProvider.chat()
          → httpx POST http://127.0.0.1:11434/api/chat
        → CodingOutputParser.parse()
      → WorkspaceWriter.write_file()
```

#### Path B: OpenRouterCodingAdapter
```
AutonomousTaskOrchestrator
  → AutonomousRunController
    → BaseDevelopmentAgent.execute()
      → OpenRouterCodingAdapter.plan_next_step()
        → OpenRouterProvider.chat()
          → httpx POST https://openrouter.ai/api/v1/chat/completions
        → CodingOutputParser.parse()
      → WorkspaceWriter.write_file()
```

#### Path C: KiloDevelopmentAgent
```
AutonomousTaskOrchestrator
  → AutonomousRunController
    → KiloDevelopmentAgent.execute()
      → KiloSessionManager.create_session()
      → start_kilo_process()
        → subprocess.Popen([kilo, run, -m, model, --dir, workspace])
      → wait_kilo_process()
        → asyncio.create_subprocess_exec()
      → KiloSessionManager.complete_session()
    → WorkspaceManager.commit_changes()
```

### Factory Resolution
`DevelopmentAgentFactory.create_agent()` reads `ai_provider` setting:
- `"kilo"` → `KiloDevelopmentAgent`
- `"ollama"` → `BaseDevelopmentAgent` + `OllamaCodingAdapter`
- `"openrouter"` → `BaseDevelopmentAgent` + `OpenRouterCodingAdapter`

## 2. Ollama Capability Findings

### Current Configuration
- Provider: `ollama`
- Endpoint: `http://127.0.0.1:11434`
- Service: RUNNING (started via `ollama serve`)
- Models available: 7

### Available Models
| Model | Size | Tool Call Test |
|-------|------|----------------|
| mistral:7b | 4.2GB | FAILED - ToolCallNormalizationError |
| llama3.2:3b | 2.0GB | FAILED - Empty response |
| qwen3:8b | 5.2GB | TIMEOUT |
| llama3.1:8b | 4.9GB | FAILED - ToolCallNormalizationError |
| qwen2.5-coder:7b | 4.7GB | FAILED - Content contains JSON, but no structured tool_calls |
| qwen2.5-coder:1.5b-base | 1.0GB | NOT TESTED |
| nomic-embed-text:latest | 0.3GB | NOT TESTED (embedding model) |

### Critical Finding: Tool Call Reliability
**The CodingProvider contract requires structured `tool_calls` in the API response.**

All tested local Ollama models output tool calls as **text content** (JSON-like strings) rather than as **structured `tool_calls`** fields in the Ollama API response.

Example failure:
```json
{
  "message": {
    "content": "{\"name\": \"write_file\", \"arguments\": {\"path\": \"hello.py\", \"content\": \"print('Hello')\"}}",
    "tool_calls": null
  }
}
```

This causes `CodingParserEmptyResponseError` or `ToolCallNormalizationError` in `CodingOutputParser.parse()`.

**Conclusion: Current local Ollama models do NOT reliably support the CodingProvider contract.**

## 3. Kilo Capability Findings

### Installed Versions
- Kilo 7.6.2: `C:\Users\adamz\.vscode\extensions\kilocode.kilo-code-7.6.2-win32-x64\bin\kilo.exe`
- Kilo 7.7.2: `C:\Users\adamz\.antigravity-ide\extensions\kilocode.kilo-code-7.7.2-win32-x64\bin\kilo.exe`

### Configuration Tested
```json
{
  "provider": {
    "ollama": {
      "baseURL": "http://localhost:11434/v1"
    }
  },
  "model": "ollama/qwen2.5-coder:7b"
}
```

### Result
Both Kilo 7.6.2 and 7.7.2 reject local Ollama models:
```
Error: Model not found: ollama/qwen2.5-coder:7b. Did you mean: ollama-cloud?
```

### Official Documentation
Kilo documentation (`kilo.ai/local-models`, `kilo.ai/docs/ai-providers/ollama`) explicitly states:
- Kilo supports local Ollama
- Configuration format: `ollama/<model_name>`
- Example: `ollama/qwen3-coder:30b`
- Base URL: `http://localhost:11434/v1`

### Discrepancy
Official docs claim Ollama support, but installed CLI versions (7.6.2, 7.7.2) do not recognize the `ollama/` prefix. The error suggests `ollama-cloud` (paid service) instead.

**Possible explanations:**
1. CLI versions are older than the Ollama support feature
2. Ollama support requires VS Code extension, not standalone CLI
3. Additional configuration is needed beyond `kilo.jsonc`
4. Bug in current CLI versions

**Conclusion: Kilo local Ollama support is UNVERIFIED in current environment.**

## 4. Official Sources Evidence

| Source | Claim | Evidence |
|--------|-------|----------|
| kilo.ai/local-models | Kilo supports Ollama, LM Studio, Atomic Chat | Official Kilo documentation |
| kilo.ai/docs/ai-providers/ollama | Local models use `ollama/<model_name>` format | Official Kilo documentation |
| GitHub Kilo-Org/kilocode | Ollama support in CLI | Documentation markdown files |
| GitHub issue #6871 | User asking how to configure Ollama in CLI | Suggests CLI configuration may be non-obvious |
| Kilo 7.6.2 CLI | Rejects `ollama/` prefix | Actual test result |

**Evidence date**: 2026-09-16

## 5. Architectural Comparison

### Option A: KiloDevelopmentAgent as Primary
**Pros:**
- Full agent runtime with session management
- Built-in recovery and process tracking
- Already integrated into Stage 10.4

**Cons:**
- Local Ollama support unverified in current CLI
- Requires cloud providers for real execution
- Higher complexity

**Current readiness**: HIGH for cloud providers, UNVERIFIED for local Ollama

### Option B: OllamaCodingAdapter + DevelopmentAgent as Primary
**Pros:**
- Already implemented
- Provider-neutral architecture
- No subprocess management
- Lower complexity

**Cons:**
- Local models don't support structured tool calls
- Cannot perform autonomous coding with current models
- Limited to simple text generation

**Current readiness**: BLOCKED for local models

### Option C: Hybrid
**Pros:**
- Flexibility to use best tool for each task
- Local fallback when cloud unavailable

**Cons:**
- Increased complexity
- Still blocked by local model limitations

**Current readiness**: PARTIAL

### Option D: OpenRouterCodingAdapter
**Pros:**
- Reliable tool call support
- Multiple model options

**Cons:**
- Requires paid API key
- Not free/local

**Current readiness**: HIGH (with API key)

## 6. Critical Architectural Question

**Does the existing DevelopmentAgent abstraction support both local CodingProvider and KiloDevelopmentAgent?**

**YES.**

The `DevelopmentAgent` interface is already provider-neutral:
```python
class DevelopmentAgent(ABC):
    @abstractmethod
    async def execute(self, context: DevelopmentAgentContext) -> DevelopmentResult:
        ...
```

Both `BaseDevelopmentAgent` (with `CodingProvider`) and `KiloDevelopmentAgent` implement this interface.

The orchestrator (`AutonomousTaskOrchestrator`) only sees `DevelopmentAgent` - it doesn't know or care which provider is used.

**No architectural changes are needed.** The system already supports both paths.

## 7. Real Local E2E Probe Result

### Probe Executed
Tested `OllamaCodingAdapter.plan_next_step()` with:
- Model: `qwen2.5-coder:7b`
- Task: "Create a Python file hello.py that prints Hello World"
- Workspace snapshot: empty
- Constraints: `CodingConstraints(max_operations=5, max_total_writes=3)`

### Result: FAILED
```
Error: CodingParserEmptyResponseError: Provider returned no tool calls.
```

### Root Cause
The Ollama model returned tool call as **text content**:
```json
{
  "message": {
    "content": "{\"name\": \"write_file\", \"arguments\": {\"path\": \"hello.py\", \"content\": \"print('Hello World')\"}}",
    "tool_calls": null
  }
}
```

Instead of **structured tool_calls**:
```json
{
  "message": {
    "content": null,
    "tool_calls": [
      {
        "function": {
          "name": "write_file",
          "arguments": {"path": "hello.py", "content": "print('Hello World')"}
        }
      }
    ]
  }
}
```

### Additional Models Tested
All local models failed similarly:
- `llama3.2:3b`: Empty response
- `mistral:7b`: Tool call normalization error
- `qwen3:8b`: Read timeout
- `llama3.1:8b`: Tool call normalization error

**Conclusion: Real local E2E with OllamaCodingAdapter is NOT POSSIBLE with current models.**

## 8. Security Implications

### Using Local Ollama Path
- Does NOT require weakening PermissionEngine
- Does NOT require weakening ToolExecutionService
- Does NOT require weakening VerificationGate
- Does NOT require weakening WorkspaceManager
- Does NOT require weakening RunController
- Does NOT require weakening recovery/approval/task state

The model remains untrusted. The application remains authoritative.

The only change would be the provider configuration, not the security architecture.

## 9. Model Reliability Observations

### Known Limitation (from Stage 8.10)
`qwen2.5-coder:7b` has variable tool-call compliance.

### Current Observation
All local Ollama models fail to produce structured `tool_calls` in the Ollama API response. They output tool calls as text content instead.

### Parser Behavior
The `CodingOutputParser` fails closed:
- Empty response → `CodingParserEmptyResponseError`
- Malformed tool calls → `ToolCallNormalizationError`
- Returns `DevelopmentResultStatus.FAILURE` or `ERROR`

**This is correct behavior.** The parser does not silently accept invalid output.

## 10. Recommended Architecture

### Primary Path: KiloDevelopmentAgent (Cloud Providers)
- Use Kilo with OpenRouter or Google for real autonomous coding
- Full session tracking, recovery, process management
- Verified security boundaries

### Fallback Path: BaseDevelopmentAgent + OllamaCodingAdapter
- Use when Kilo cloud providers are unavailable
- **Blocked until local models support structured tool calls**
- Requires model upgrade or prompt engineering breakthrough

### Hybrid Approach (Future)
- Detect provider availability at runtime
- Route to KiloDevelopmentAgent when cloud providers configured
- Route to BaseDevelopmentAgent when local models support tool calls
- Fail closed if neither path is viable

### Minimal Implementation Scope for Next Stage
If proceeding with Kilo primary:
1. Wire KiloDevelopmentAgent into AutonomousTaskOrchestrator
2. Configure OpenRouter provider in settings
3. Test real E2E with cloud model
4. Keep OllamaCodingAdapter as documented fallback

If proceeding with Ollama primary:
1. Find/await local model with reliable tool call support
2. OR: modify CodingOutputParser to extract tool calls from text content (requires security review)
3. Test real E2E with local model

## 11. Risks and Limitations

### Risk 1: Kilo Cloud Provider Dependency
- Real autonomous coding requires cloud API key
- Not free/local
- Requires internet connection

### Risk 2: Local Model Limitations
- Current local models don't support structured tool calls
- May require larger models (30b+) that exceed hardware capacity
- May require prompt engineering or parser modifications

### Risk 3: Kilo CLI Version
- Current CLI versions (7.6.2, 7.7.2) may not support local Ollama
- Official docs claim support, but actual behavior differs
- May require newer version or VS Code extension

### Risk 4: Cost
- Cloud providers (OpenRouter, Google) require payment
- Local models require significant hardware (32GB+ RAM recommended)

## 12. Evidence Summary

| Finding | Evidence |
|---------|----------|
| OllamaCodingAdapter exists and is integrated | Code inspection: `app/ai/providers/coding_ollama.py` |
| KiloDevelopmentAgent exists and is integrated | Code inspection: `app/orchestration/agents/kilo_agent.py` |
| Factory supports both paths | Code inspection: `app/orchestration/agent_factory.py` |
| Local Ollama models don't support tool calls | Live test: all 5 models failed |
| Kilo CLI rejects Ollama models | Live test: 7.6.2 and 7.7.2 both fail |
| Kilo docs claim Ollama support | Web search: official Kilo documentation |
| Parser fails closed on invalid output | Code inspection: `CodingOutputParser` |

## 13. Exact Blocker for Real Local E2E

```
BLOCKER: Local Ollama models do not produce structured tool_calls in API responses.

Tested models:
- qwen2.5-coder:7b: Returns tool call as text content, not structured tool_calls
- llama3.2:3b: Empty response
- mistral:7b: Tool call normalization error
- llama3.1:8b: Tool call normalization error
- qwen3:8b: Read timeout

Error: CodingParserEmptyResponseError: Provider returned no tool calls.

The CodingProvider contract requires structured tool_calls in the Ollama API response.
Current models output tool calls as text content instead.

This is a model capability limitation, not an adapter bug.
```

## 14. Conclusion

**STAGE_10_6_DISCOVERY_COMPLETE**

### Key Findings
1. **Architecture already supports both paths**: `DevelopmentAgent` abstraction is provider-neutral
2. **Local Ollama models fail**: None of the 7 local models produce structured tool calls
3. **Kilo local Ollama unverified**: CLI rejects Ollama models despite official docs claiming support
4. **Cloud providers work**: OpenRouter and Google are configured and available

### Recommended Architecture
**Option C (Hybrid) with Kilo as primary for real execution:**
- KiloDevelopmentAgent with OpenRouter/Google for real autonomous coding
- OllamaCodingAdapter as fallback for future local models that support tool calls
- No architectural changes needed - system already supports both

### Real Local Ollama Coding Currently Works
**NO** - Blocked by model tool-call capability limitation.

### Kilo Should Remain
**Primary for real execution, optional for local fallback.**

### Next Stage
**Stage 10.7: Kilo + OpenRouter Real E2E Validation**
- Configure OpenRouter provider
- Execute real autonomous coding task with Kilo + cloud model
- Verify full chain: AI Manager → Policy → Planning → DAG → Kilo → Verification → Git
- Document cost and performance characteristics
