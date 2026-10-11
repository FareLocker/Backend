import { GoogleGenAI, Type } from "@google/genai/node";

const ai = new GoogleGenAI({ apiKey: process.env.GEMINI_API_KEY });
const model = process.env.GEMINI_MODEL ?? "gemini-2.5-flash";

const apiUrl = process.env.FARELOCK_API_URL ?? "http://127.0.0.1:8000";
const secret = process.env.PHOTON_INTERNAL_SECRET ?? process.env.PHOTON_MESSAGING_SECRET;

const instructions = `You are FareLocker's assistant, texting over iMessage.
FareLocker sells FareLocks: after booking a flight, you pay a small fee, and if
the fare drops before you fly, FareLocker pays you the difference (up to a limit).
Keep replies short and friendly, like a text message. No markdown.
Never make up prices. To get a price, use the get_farelock_quote tool. If the user
hasn't given route, date, flight number and what they paid, ask for the missing
pieces first. If the tool returns an error, say so plainly and don't guess.`;

const tools = [
  {
    functionDeclarations: [
      {
        name: "get_farelock_quote",
        description: "Gets a FareLock quote for a booked flight.",
        parameters: {
          type: Type.OBJECT,
          properties: {
            origin: { type: Type.STRING, description: "Origin airport code, e.g. ATL" },
            destination: { type: Type.STRING, description: "Destination airport code, e.g. LAX" },
            date: { type: Type.STRING, description: "Departure date, YYYY-MM-DD" },
            flightNumber: { type: Type.STRING, description: "e.g. DL123" },
            pricePaid: { type: Type.NUMBER, description: "Price the user paid, in USD" },
          },
          required: ["origin", "destination", "date", "flightNumber", "pricePaid"],
        },
      },
    ],
  },
];

const handlers: Record<string, (args: any) => Promise<unknown>> = {
  get_farelock_quote: async (args) => {
    try {
      const r = await fetch(`${apiUrl}/quote`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-internal-secret": secret ?? "",
        },
        body: JSON.stringify(args),
        signal: AbortSignal.timeout(8000),
      });
      if (!r.ok) return { error: `FareLock API returned ${r.status}` };
      return await r.json();
    } catch {
      return { error: "FareLock API unreachable" };
    }
  },
};

const history = new Map<string, any[]>();

export async function runAgent(chatId: string, text: string): Promise<string> {
  const contents = history.get(chatId) ?? [];
  contents.push({ role: "user", parts: [{ text }] });

  for (let i = 0; i < 5; i++) {
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

    const candidate = response.candidates?.[0];
    const content = candidate?.content;
    if (content) {
      contents.push(content);
    }
    const parts = await Promise.all(
      calls.map(async (c) => {
        const fn = handlers[c.name!];
        const result = fn ? await fn(c.args) : { error: "unknown tool" };
        return { functionResponse: { name: c.name!, response: { result } } };
      })
    );
    contents.push({ role: "user", parts });
  }
  return "Sorry, that took too many steps. Try again?";
}