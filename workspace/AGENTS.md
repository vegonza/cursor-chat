# CRITICAL INSTRUCTION — READ THIS FIRST

You are communicating with the user through a tool-call-based interface, NOT through the terminal.

## MANDATORY: Use the `respond` MCP tool

**You MUST call the `respond` tool from the `prompt` MCP server at the END of EVERY single turn.**

- Put your ENTIRE response inside the `response` argument of the tool call.
- Do NOT write your response as plain text output. The user CANNOT see plain text output.
- The ONLY way the user sees your response is through the `respond` tool call.
- If you output text without calling `respond`, the user sees nothing — they just see "Thinking..." forever.

### How it works:
1. The `respond` tool sends your response to the browser chat UI.
2. It then WAITS for the user to type their next message.
3. It RETURNS the user's message to you as the tool result.
4. You process their message and call `respond` again.

### Example:
```
Tool call: prompt.respond(response: "Here is my answer to your question...")
→ Tool returns: "Thanks, now can you also check..."
```

**NEVER skip calling `respond`. NEVER output your answer as regular text. ALWAYS use the tool.**

## Do NOT ask "What now?" or present options

Do NOT use the AskQuestion tool or any similar pattern. Do NOT end your response with "What now?" and multiple choice options. Just provide your answer in the `respond` tool and wait for the user to type their own follow-up naturally.
