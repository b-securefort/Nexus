// Static, time-of-day greeting pools for the empty-state hero. One entry is
// picked at random per session — no backend call, no loading skeleton.
//
// Buckets: morning 05:00–11:59 · afternoon 12:00–16:59 · evening 17:00–20:59
// · night 21:00–04:59.

export const GREETINGS = {
  morning: [
    "Good morning! The cloud's been up all night — let's see what it did.",
    "Morning! Coffee first, then commands.",
    "Rise and shine — your subscriptions survived the night.",
    "Good morning. Zero incidents is a great way to start the day.",
    "Morning! What shall we build before lunch?",
    "Good morning — let's make today boring, in the best zero-outage way.",
    "Top of the morning! Runbooks ready when you are.",
    "Morning! Let's turn coffee into infrastructure.",
    "Good morning — fresh quota, clean slate.",
    "Good morning! What's first on the plate today?",
    "Morning! The dashboards are green and the day is yours.",
    "Good morning — let's get the important things done before the meetings find you.",
    "Rise and build! The pipelines are warmed up.",
    "Morning! Today's forecast: clear skies, scattered deployments.",
    "Good morning — small commits, big wins. Where do we start?",
  ],
  afternoon: [
    "Good afternoon! Halfway there — what's next?",
    "Afternoon! Need a hand with the post-lunch backlog?",
    "Good afternoon — the cloud waits for no one.",
    "Afternoon check-in: everything still green?",
    "Good afternoon! Let's ship something worth mentioning at standup.",
    "Afternoon! Big plans or quick wins?",
    "Good afternoon — peak hours, let's make them count.",
    "Afternoon! What can I take off your plate?",
    "Good afternoon. Deploy now, relax later?",
    "Afternoon! The knowledge base and I are at your service.",
    "Good afternoon — momentum looks good, let's keep it.",
    "Afternoon! One well-placed query can save the whole day.",
    "Good afternoon — meetings done? Let's do real work.",
    "Afternoon! Caffeine levels dropping, automation levels rising.",
    "Good afternoon — what's blocking you? Let's unblock it.",
  ],
  evening: [
    "Good evening! Wrapping up or just warming up?",
    "Evening! Let's land this day smoothly.",
    "Good evening — one more task before sign-off?",
    "Evening! The quiet hours are great for clean deploys.",
    "Good evening. Anything left on the checklist?",
    "Evening! Let's finish strong.",
    "Good evening — low traffic, perfect for maintenance.",
    "Evening check-in: what needs a second pair of eyes?",
    "Good evening! Tie up loose ends, or plan tomorrow?",
    "Evening! I'll keep the lights on — what do you need?",
    "Good evening — the best documentation gets written at this hour.",
    "Evening! Quick win before you log off?",
    "Good evening — calm seas, steady deploys.",
    "Evening! Let's leave tomorrow-you a clean slate.",
    "Good evening — anything worth automating before you head out?",
  ],
  night: [
    "Burning the midnight oil? I'm wide awake.",
    "Hey night owl — what are we fixing?",
    "Late night session? Let's make it count.",
    "The cloud never sleeps — apparently neither do you.",
    "Quiet hours, clear logs. What's up?",
    "Midnight deploys are bold. I like it.",
    "Still here? Let's get you to bed faster — what do you need?",
    "Night shift activated. How can I help?",
    "Late-night ideas are the best ideas. Try me.",
    "Insomnia or incident? Either way, I've got you.",
    "After hours — where the real engineering happens.",
    "Night mode: fewer interruptions, faster answers.",
    "The servers hum, the logs scroll, and here we are.",
    "Late shift? I'll take the first watch.",
    "Dark outside, dashboards glowing. What's the mission?",
  ],
} as const;

export type DayPart = keyof typeof GREETINGS;

export function dayPart(hour: number): DayPart {
  if (hour >= 5 && hour < 12) return "morning";
  if (hour >= 12 && hour < 17) return "afternoon";
  if (hour >= 17 && hour < 21) return "evening";
  return "night";
}

export function pickGreeting(date: Date = new Date()): string {
  const pool = GREETINGS[dayPart(date.getHours())];
  return pool[Math.floor(Math.random() * pool.length)];
}
