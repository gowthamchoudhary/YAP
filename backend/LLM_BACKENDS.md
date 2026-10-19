# Text LLM backends

Set `LLM_BACKEND` in `backend/.env` to choose the text-generation provider:

```env
LLM_BACKEND=groq
GROQ_API_KEY=...
GROQ_TEXT_MODEL=openai/gpt-oss-120b
ASSEMBLYAI_API_KEY=...
GATEWAY_TEXT_MODEL=qwen3.5-4b-32k-fast
```

`LLM_BACKEND` defaults to `groq`, preserving the existing behavior. Set it to
`gateway` to send text requests to AssemblyAI LLM Gateway. The Gateway model
defaults to `qwen3.5-4b-32k-fast` and can be changed with
`GATEWAY_TEXT_MODEL`.

When Groq is selected, a failed text completion is retried once through the
Gateway. This fallback needs `ASSEMBLYAI_API_KEY`; for compatibility with the
security-test setup, `AAI_TEST_KEY` is also accepted. Vision requests continue
to use Groq. Each generated line's debug object reports `llm_backend` as
`groq` or `gateway`, showing which provider returned the accepted text.
