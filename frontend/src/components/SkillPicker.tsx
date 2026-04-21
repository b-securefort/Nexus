import { useEffect, useState } from "react";
import { ChevronDown, Sparkles, User as UserIcon } from "lucide-react";
import { fetchSkills } from "../api/skills";
import { useAppStore } from "../store/useAppStore";
import type { Skill } from "../types";

export function SkillPicker() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [open, setOpen] = useState(false);
  const { selectedSkillId, setSelectedSkillId, conversationId } = useAppStore();

  useEffect(() => {
    fetchSkills()
      .then((loaded) => {
        setSkills(loaded);
        // Auto-select default skill if none selected
        if (!selectedSkillId && loaded.length > 0) {
          const defaultSkill =
            loaded.find((s) => s.id === "shared:chat-with-kb") || loaded[0];
          setSelectedSkillId(defaultSkill.id);
        }
      })
      .catch(() => {});
  }, []);

  const selected = skills.find((s) => s.id === selectedSkillId);
  const sharedSkills = skills.filter((s) => s.source === "shared");
  const personalSkills = skills.filter((s) => s.source === "personal");

  // Skill is locked for existing conversations
  const locked = conversationId !== null;

  return (
    <div className="relative">
      <button
        onClick={() => !locked && setOpen(!open)}
        disabled={locked}
        className="flex items-center gap-2 bg-zinc-800 border border-zinc-600 rounded-lg px-3 py-2 text-sm text-zinc-200 hover:bg-zinc-700 disabled:opacity-60 disabled:cursor-not-allowed transition-colors min-w-[200px]"
      >
        <Sparkles className="w-4 h-4 text-blue-400" />
        <span className="flex-1 text-left truncate">
          {selected ? selected.display_name : "Select a skill..."}
        </span>
        {!locked && <ChevronDown className="w-4 h-4 text-zinc-400" />}
      </button>

      {open && (
        <>
          <div
            className="fixed inset-0 z-10"
            onClick={() => setOpen(false)}
          />
          <div className="absolute top-full left-0 mt-1 w-64 bg-zinc-800 border border-zinc-600 rounded-lg shadow-xl z-20 overflow-hidden">
            {sharedSkills.length > 0 && (
              <>
                <div className="px-3 py-1.5 text-xs font-medium text-zinc-500 uppercase tracking-wider bg-zinc-900">
                  Shared (Team)
                </div>
                {sharedSkills.map((skill) => (
                  <button
                    key={skill.id}
                    onClick={() => {
                      setSelectedSkillId(skill.id);
                      setOpen(false);
                    }}
                    className="w-full text-left px-3 py-2 text-sm text-zinc-200 hover:bg-zinc-700 flex items-center gap-2"
                  >
                    <Sparkles className="w-3.5 h-3.5 text-blue-400" />
                    <div>
                      <div>{skill.display_name}</div>
                      {skill.description && (
                        <div className="text-xs text-zinc-500 truncate">
                          {skill.description}
                        </div>
                      )}
                    </div>
                  </button>
                ))}
              </>
            )}

            {personalSkills.length > 0 && (
              <>
                <div className="px-3 py-1.5 text-xs font-medium text-zinc-500 uppercase tracking-wider bg-zinc-900">
                  My Skills
                </div>
                {personalSkills.map((skill) => (
                  <button
                    key={skill.id}
                    onClick={() => {
                      setSelectedSkillId(skill.id);
                      setOpen(false);
                    }}
                    className="w-full text-left px-3 py-2 text-sm text-zinc-200 hover:bg-zinc-700 flex items-center gap-2"
                  >
                    <UserIcon className="w-3.5 h-3.5 text-green-400" />
                    <div>
                      <div>{skill.display_name}</div>
                      {skill.description && (
                        <div className="text-xs text-zinc-500 truncate">
                          {skill.description}
                        </div>
                      )}
                    </div>
                  </button>
                ))}
              </>
            )}

            {skills.length === 0 && (
              <div className="px-3 py-4 text-sm text-zinc-500 text-center">
                No skills available
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
