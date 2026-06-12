import { useState } from "react";
import { HelpCircle, Check } from "lucide-react";
import type {
  QuestionAnswerEntry,
  QuestionInfo,
  QuestionItem,
} from "../types";

interface Props {
  question: QuestionInfo;
  // Pre-resolved answers freeze the card into a read-only state. Used in
  // historical messages and immediately after the user submits.
  resolved?: QuestionAnswerEntry[];
  onSubmit?: (answers: QuestionAnswerEntry[]) => void;
}

const OTHER_LABEL = "Other";

interface PerQuestionState {
  // Selected option labels (for radio: max 1; for multi: 0..N).
  selected: Set<string>;
  // Free-text content when "Other" is picked.
  otherText: string;
}

function initialState(items: QuestionItem[]): PerQuestionState[] {
  return items.map(() => ({ selected: new Set<string>(), otherText: "" }));
}

function buildAnswers(
  items: QuestionItem[], state: PerQuestionState[]
): QuestionAnswerEntry[] | null {
  const out: QuestionAnswerEntry[] = [];
  for (let i = 0; i < items.length; i++) {
    const q = items[i];
    const s = state[i];
    if (s.selected.size === 0) return null; // missing answer
    const selected = Array.from(s.selected);
    const entry: QuestionAnswerEntry = {
      question: q.question,
      selected,
    };
    if (s.selected.has(OTHER_LABEL)) {
      const trimmed = s.otherText.trim();
      if (!trimmed) return null; // "Other" picked but no free-text
      entry.notes = trimmed;
    }
    out.push(entry);
  }
  return out;
}

export function QuestionCard({ question, resolved, onSubmit }: Props) {
  const isLocked = !!resolved;
  const [state, setState] = useState<PerQuestionState[]>(() =>
    initialState(question.questions)
  );
  const [submitting, setSubmitting] = useState(false);

  const toggleOption = (qIdx: number, label: string, multi: boolean) => {
    setState((prev) => {
      const next = prev.map((s) => ({
        selected: new Set(s.selected),
        otherText: s.otherText,
      }));
      const s = next[qIdx];
      if (multi) {
        if (s.selected.has(label)) s.selected.delete(label);
        else s.selected.add(label);
      } else {
        s.selected = new Set([label]);
      }
      return next;
    });
  };

  const setOtherText = (qIdx: number, text: string) => {
    setState((prev) => {
      const next = prev.map((s) => ({
        selected: new Set(s.selected),
        otherText: s.otherText,
      }));
      next[qIdx].otherText = text;
      return next;
    });
  };

  const ready = isLocked
    ? false
    : buildAnswers(question.questions, state) !== null;

  const handleSubmit = () => {
    if (!ready || !onSubmit) return;
    const answers = buildAnswers(question.questions, state);
    if (!answers) return;
    setSubmitting(true);
    onSubmit(answers);
  };

  // For locked (historical / just-submitted) state, show selected answers
  // alongside each option as a readable summary.
  const resolvedFor = (qIdx: number): QuestionAnswerEntry | undefined =>
    resolved?.[qIdx];

  return (
    <div className="bg-accent/5 border border-accent/25 rounded-xl p-5 space-y-4">
      <div className="flex items-center gap-2.5 text-accent-light">
        <HelpCircle className="w-5 h-5" />
        <span className="font-semibold text-sm tracking-tight">
          {isLocked ? "Your answers" : "A few questions before I begin"}
        </span>
      </div>

      {question.questions.map((q, qIdx) => {
        const s = state[qIdx];
        const r = resolvedFor(qIdx);
        const lockedSelected = new Set(r?.selected ?? []);
        const lockedNotes = r?.notes ?? "";

        const selectedSet = isLocked ? lockedSelected : s.selected;
        const otherSelected = selectedSet.has(OTHER_LABEL);

        return (
          <div key={qIdx} className="space-y-2">
            <div className="text-xs font-semibold uppercase tracking-wider text-accent-light">
              {q.header}
            </div>
            <div className="flex items-baseline gap-2 flex-wrap">
              <span className="text-base-100 text-sm font-medium">
                {q.question}
              </span>
              {q.multi_select && (
                <span className="text-[10px] text-base-500 italic ml-1">
                  (pick one or more)
                </span>
              )}
            </div>

            <div className="space-y-1.5">
              {q.options.map((opt, optIdx) => {
                const checked = selectedSet.has(opt.label);
                const ControlIcon = q.multi_select ? CheckboxIcon : RadioIcon;
                return (
                  <button
                    key={optIdx}
                    type="button"
                    disabled={isLocked}
                    onClick={() => toggleOption(qIdx, opt.label, q.multi_select)}
                    className={`w-full text-left flex items-start gap-3 px-3 py-2 rounded-lg border transition-colors duration-100 ${
                      checked
                        ? "bg-accent/15 border-accent/50"
                        : "bg-base-900/40 border-base-700/40 hover:border-base-600/60"
                    } ${isLocked ? "cursor-default opacity-90" : "cursor-pointer"}`}
                  >
                    <ControlIcon checked={checked} className="mt-0.5 flex-shrink-0" />
                    <div className="min-w-0">
                      <div className="text-sm text-base-100">{opt.label}</div>
                      {opt.description && (
                        <div className="text-xs text-base-400 mt-0.5">
                          {opt.description}
                        </div>
                      )}
                    </div>
                  </button>
                );
              })}

              {/* Always offer an "Other" escape hatch so the user isn't trapped.
                  The toggle row and the free-text input are siblings, NOT
                  nested — a <button> ancestor treats spacebar as activation,
                  so any space typed inside the textarea would bubble up and
                  uncheck the option. */}
              <div
                className={`w-full rounded-lg border transition-colors duration-100 ${
                  otherSelected
                    ? "bg-accent/15 border-accent/50"
                    : "bg-base-900/40 border-base-700/40 hover:border-base-600/60"
                }`}
              >
                <button
                  type="button"
                  disabled={isLocked}
                  onClick={() => toggleOption(qIdx, OTHER_LABEL, q.multi_select)}
                  className={`w-full text-left flex items-start gap-3 px-3 py-2 ${
                    isLocked ? "cursor-default opacity-90" : "cursor-pointer"
                  }`}
                >
                  {q.multi_select ? (
                    <CheckboxIcon checked={otherSelected} className="mt-0.5 flex-shrink-0" />
                  ) : (
                    <RadioIcon checked={otherSelected} className="mt-0.5 flex-shrink-0" />
                  )}
                  <div className="text-sm text-base-100">Other</div>
                </button>
                {otherSelected && (
                  <div className="px-3 pb-2.5 pl-10">
                    <textarea
                      value={isLocked ? lockedNotes : s.otherText}
                      readOnly={isLocked}
                      onChange={(e) => setOtherText(qIdx, e.target.value)}
                      placeholder="Type your answer..."
                      rows={2}
                      className="w-full bg-base-900 border border-base-700 rounded-md px-2 py-1.5 text-sm text-base-100 placeholder:text-base-500 focus:border-accent/60 focus:outline-none resize-y"
                    />
                  </div>
                )}
              </div>
            </div>
          </div>
        );
      })}

      {!isLocked && (
        <div className="flex justify-end pt-1">
          <button
            type="button"
            onClick={handleSubmit}
            disabled={!ready || submitting}
            className="flex items-center gap-2 bg-accent hover:bg-accent-hover disabled:bg-base-800 disabled:text-base-600 text-white px-4 py-2 rounded-xl transition-[background-color,transform] duration-150 ease-[var(--ease-out)] text-sm font-medium"
          >
            <Check className="w-4 h-4" />
            {submitting ? "Sending…" : "Submit answers"}
          </button>
        </div>
      )}
    </div>
  );
}

function RadioIcon({ checked, className }: { checked: boolean; className?: string }) {
  return (
    <span
      className={`inline-flex items-center justify-center w-4 h-4 rounded-full border ${
        checked ? "border-accent-light bg-accent/20" : "border-base-500"
      } ${className ?? ""}`}
    >
      {checked && <span className="w-2 h-2 rounded-full bg-accent-light" />}
    </span>
  );
}

function CheckboxIcon({ checked, className }: { checked: boolean; className?: string }) {
  return (
    <span
      className={`inline-flex items-center justify-center w-4 h-4 rounded border ${
        checked ? "border-accent-light bg-accent/20" : "border-base-500"
      } ${className ?? ""}`}
    >
      {checked && (
        <svg viewBox="0 0 12 12" className="w-2.5 h-2.5" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M2 6 L5 9 L10 3" className="text-accent-light" />
        </svg>
      )}
    </span>
  );
}
