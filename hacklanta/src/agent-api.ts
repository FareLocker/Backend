import { GoogleGenAI } from "@google/genai/node";

const ai = new GoogleGenAI({ apiKey: process.env.GEMINI_API_KEY });
const model = process.env.GEMINI_MODEL ?? "gemini-2.5-flash";

const apiUrl = process.env.FARELOCK_API_URL ?? "http://127.0.0.1:8000";
const secret = process.env.PHOTON_INTERNAL_SECRET ?? process.env.PHOTON_MESSAGING_SECRET;

const instructions = `You are FareLocker's assistant, texting over iMessage.
FareLocker sells FareLocks: after booking a flight, you pay a small fee, and if
the fare drops before you fly, FareLocker pays you the difference (up to a limit).
Keep replies short and friendly, like a text message. No markdown.
Never make up prices. If someone asks for a price, tell them to send their
route, date, flight number and what they paid.`;

export async function runAgent(text: string): Promise<string> {
  const response = await ai.models.generateContent({
    model,
    contents: text,
    config: { systemInstruction: instructions },
  });
  return response.text ?? "Sorry, I didn't catch that.";
}
