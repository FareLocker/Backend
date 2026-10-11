import { history, ai, instructions, handlers } from "./agent-api";


export async function runAgent(chatId: string, text: string): Promise<string> {
  const contents = history.get(chatId) ?? [];
  contents.push({ role: "user", parts: [{ text }] });

  for (let i = 0; i < 3; i++) {
    const response = await ai.models.generateContent({
      model,
      contents,
      config: { systemInstruction: instructions, tools },
    });

    const calls = response.functionCalls;
    if (!calls?.length) {
      const reply = response.text ?? "Sorry, I didn't catch that.";
      contents.push({ role: "model", parts: [{ text: reply }] });
      history.set(chatId, contents.slice(-20));
      return reply;
    }

    contents.push(response.candidates![0].content!);
    const parts = await Promise.all(
      calls.map(async (c) => {
        const fn = handlers[c.name!];
        const result = fn ? await fn(c.args) : {
          error: "unknown tool"
        };
        return { functionResponse: { name: c.name!, response: { result } } };
      })
    );
    contents.push({ role: "user", parts });
  }
  return "Sorry, that took too many steps. Try again";
}
