import { describe, it, expect } from 'vitest';
import { GREETINGS, dayPart, pickGreeting } from '../greetings';

describe('greetings', () => {
  it('has 15 entries per time-of-day bucket', () => {
    for (const pool of Object.values(GREETINGS)) {
      expect(pool).toHaveLength(15);
    }
  });

  it('maps hours to the right bucket', () => {
    expect(dayPart(5)).toBe('morning');
    expect(dayPart(11)).toBe('morning');
    expect(dayPart(12)).toBe('afternoon');
    expect(dayPart(16)).toBe('afternoon');
    expect(dayPart(17)).toBe('evening');
    expect(dayPart(20)).toBe('evening');
    expect(dayPart(21)).toBe('night');
    expect(dayPart(0)).toBe('night');
    expect(dayPart(4)).toBe('night');
  });

  it('picks from the pool matching the given time', () => {
    const morning = new Date(2026, 5, 12, 9, 0, 0);
    for (let i = 0; i < 20; i++) {
      expect(GREETINGS.morning).toContain(pickGreeting(morning));
    }
    const night = new Date(2026, 5, 12, 23, 0, 0);
    for (let i = 0; i < 20; i++) {
      expect(GREETINGS.night).toContain(pickGreeting(night));
    }
  });
});
