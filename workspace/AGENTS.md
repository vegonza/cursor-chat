# STOP. READ THIS ENTIRE FILE BEFORE DOING ANYTHING.

## YOU MUST CALL `next_prompt` — THIS IS NOT OPTIONAL

Every single turn you take MUST end with this tool call:

```
Tool: chat.next_prompt (no arguments)
```

### WHY:
- The user communicates with you through a web chat UI
- The user CANNOT see your response or send a new message until you call `next_prompt`
- If you don't call it, the conversation is permanently stuck
- The user will think you crashed

### RULES:
1. ALWAYS call `chat.next_prompt()` at the END of EVERY turn — no exceptions
2. Call it with NO arguments
3. Call it even if your answer is short
4. Call it even if you encountered an error
5. Call it even if you're unsure what to say
6. NEVER end your turn without calling it
7. NEVER use AskQuestion or present multiple-choice options — just answer and call `next_prompt`
8. Do NOT present "What now?" prompts

### WHAT HAPPENS IF YOU FORGET:
- The user is stuck forever waiting
- The conversation is broken
- You will never receive another message

**CALL `next_prompt` EVERY TURN. EVERY. SINGLE. TURN.**
