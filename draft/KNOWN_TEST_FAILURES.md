# Known Test Failures

## File: `tests/unit/test_claude_runner.py`

**Count:** 7 failures
**Root cause:** `CLAUDE_DISPATCH_URL=http://host.docker.internal:8101` is set in the environment. All 7 tests mock `asyncio.create_subprocess_exec` expecting local subprocess mode, but `run_claude()` sees the env var and routes to the HTTP bridge instead. The bridge is unreachable, so every call returns a connect error rather than exercising the local path.

**Fix:** Add `patch.dict("os.environ", {"CLAUDE_DISPATCH_URL": ""})` to each failing test (as is already done correctly in `test_dispatch_bridge.py`).

---

### `TestRunClaude::test_successful_run`
```
AssertionError: assert False is True
ClaudeResult(success=False, error='Cannot connect to dispatch bridge at
http://host.docker.internal:8101: [Errno -2] Name or service not known')
```

### `TestRunClaude::test_non_zero_exit`
```
AssertionError: assert None == 1
ClaudeResult(exit_code=None, error='Cannot connect to dispatch bridge ...')
```

### `TestRunClaude::test_timeout`
```
AssertionError: assert False is True  (timed_out)
ClaudeResult(timed_out=False, error='Cannot connect to dispatch bridge ...')
```

### `TestRunClaude::test_binary_not_found`
```
AssertionError: assert 'not found' in 'Cannot connect to dispatch bridge ...'
```

### `TestRunClaude::test_command_construction`
```
IndexError: list index out of range
# calls[] is empty — subprocess was never spawned because bridge was used instead
```

### `TestRunClaude::test_is_error_flag`
```
AssertionError: assert 'Cannot connect...' == 'Bad input'
```

### `TestRunClaudeSync::test_sync_wrapper`
```
AssertionError: assert False is True
ClaudeResult(success=False, error='Cannot connect to dispatch bridge ...')
```
