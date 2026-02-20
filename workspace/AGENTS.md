# CRITICAL — YOU MUST READ AND FOLLOW THIS

## THE #1 RULE: ALWAYS CALL `send_message`

You communicate through a BROWSER CHAT, not the terminal. The user CANNOT see terminal output.

**EVERY SINGLE TURN must end with you calling the `send_message` tool from the `chat` MCP server.**

There are ZERO exceptions. Even if your answer is short. Even if you're unsure. Even if you encountered an error. ALWAYS call `send_message`.

### What happens if you don't call `send_message`:
- The user sees "Thinking..." FOREVER
- Your work is INVISIBLE to them
- The conversation is STUCK

### How to use it:
```
Tool call: chat.send_message(message: "Your complete answer goes here")
-> Returns: the user's next message
```

Put your ENTIRE answer in the `message` argument. Do NOT write text output separately. ALL of your response must go inside the tool call.

### Checklist before ending your turn:
1. Did I call `chat.send_message`? If NO -> call it NOW
2. Is my full response in the `message` argument? If NO -> put it there
3. Am I NOT outputting text outside the tool call? If outputting -> move it into the tool call

**NEVER skip `send_message`. NEVER. NOT ONCE. ALWAYS CALL IT.**

## Do NOT use AskQuestion

Do NOT present "What now?" with options. Do NOT use AskQuestion. Just answer and call `send_message`. The user will type their own follow-up.
