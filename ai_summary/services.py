from ollama import chat


def summarize_message(message_text):
    prompt = f"""
Summarize this email in 2-3 short sentences.

Output ONLY the summary.
Do not explain your reasoning.
Do not analyze the email.

Email:
{message_text}
"""

    response = chat(
        model="llama3.2:3b",
        messages=[
            {
                "role": "user",
                "content": prompt,
            }
        ],
    )

    return response.message.content.strip()