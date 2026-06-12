import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, Plus, Pencil, Trash2, GraduationCap, User } from "lucide-react";
import { fetchSkills, deletePersonalSkill } from "../api/skills";
import { SkillEditor } from "../components/SkillEditor";
import type { Skill } from "../types";

export function SkillsPage() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [editing, setEditing] = useState<string | null>(null); // null = not editing, "" = create, "name" = edit
  const [loading, setLoading] = useState(true);

  const loadSkills = () => {
    setLoading(true);
    fetchSkills()
      .then((s) => {
        setSkills(s);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  };

  useEffect(() => {
    loadSkills();
  }, []);

  const sharedSkills = skills.filter((s) => s.source === "shared");
  const personalSkills = skills.filter((s) => s.source === "personal");

  const handleDelete = async (name: string) => {
    if (!confirm("Delete this skill? Past conversations using it will still work.")) return;
    try {
      await deletePersonalSkill(name);
      loadSkills();
    } catch {
      // Ignore
    }
  };

  if (editing !== null) {
    return (
      <div className="min-h-screen bg-base-950 text-base-100">
        <div className="max-w-2xl mx-auto px-6 py-10">
          <SkillEditor
            skillName={editing || null}
            onSaved={() => {
              setEditing(null);
              loadSkills();
            }}
            onCancel={() => setEditing(null)}
          />
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-base-950 text-base-100">
      <div className="max-w-3xl mx-auto px-6 py-10">
        {/* Header */}
        <div className="flex items-center gap-4 mb-10">
          <Link
            to="/"
            className="text-base-500 hover:text-base-300 transition-colors p-1"
          >
            <ArrowLeft className="w-5 h-5" />
          </Link>
          <h1 className="text-xl font-semibold tracking-tight">Skills</h1>
          <button
            onClick={() => setEditing("")}
            className="ml-auto flex items-center gap-2 bg-accent hover:bg-accent-hover text-white rounded-xl px-4 py-2 text-sm font-medium transition-[background-color,transform] duration-150 ease-[var(--ease-out)]"
          >
            <Plus className="w-4 h-4" />
            New Skill
          </button>
        </div>

        {/* Shared skills */}
        <section className="mb-10">
          <h2 className="text-[11px] font-medium text-base-500 uppercase tracking-wider mb-4">
            Shared (Team)
          </h2>
          {sharedSkills.length === 0 && (
            <p className="text-base-600 text-sm">No shared skills available.</p>
          )}
          <div className="space-y-2">
            {sharedSkills.map((skill) => (
              <div
                key={skill.id}
                className="bg-base-900 border border-base-800/80 rounded-xl px-4 py-3.5"
              >
                <div className="flex items-start gap-3">
                  <div className="w-7 h-7 rounded-lg bg-accent/10 flex items-center justify-center flex-shrink-0">
                    <GraduationCap className="w-3.5 h-3.5 text-accent-light" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium text-base-200">
                      {skill.display_name}
                    </div>
                    {skill.description && (
                      <div className="text-xs text-base-500 mt-0.5 leading-relaxed">
                        {skill.description}
                      </div>
                    )}
                  </div>
                </div>
                {skill.tools.length > 0 && (
                  <div className="flex gap-1 flex-wrap mt-2.5 pl-10">
                    {skill.tools.map((t) => (
                      <span
                        key={t}
                        className="text-[11px] bg-base-800 text-base-500 px-1.5 py-0.5 rounded-md font-mono"
                      >
                        {t}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </section>

        {/* Personal skills */}
        <section>
          <h2 className="text-[11px] font-medium text-base-500 uppercase tracking-wider mb-4">
            My Skills
          </h2>
          {personalSkills.length === 0 && (
            <p className="text-base-600 text-sm">
              No personal skills yet. Click "New Skill" to create one.
            </p>
          )}
          <div className="space-y-2">
            {personalSkills.map((skill) => (
              <div
                key={skill.id}
                className="bg-base-900 border border-base-800/80 rounded-xl px-4 py-3.5"
              >
                <div className="flex items-start gap-3">
                  <div className="w-7 h-7 rounded-lg bg-success/10 flex items-center justify-center flex-shrink-0">
                    <User className="w-3.5 h-3.5 text-success" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium text-base-200">
                      {skill.display_name}
                    </div>
                    {skill.description && (
                      <div className="text-xs text-base-500 mt-0.5 leading-relaxed">
                        {skill.description}
                      </div>
                    )}
                  </div>
                  <button
                    onClick={() => setEditing(skill.name)}
                    className="text-base-500 hover:text-base-300 p-1.5 transition-colors flex-shrink-0"
                  >
                    <Pencil className="w-3.5 h-3.5" />
                  </button>
                  <button
                    onClick={() => handleDelete(skill.name)}
                    className="text-base-500 hover:text-danger p-1.5 transition-colors flex-shrink-0"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
                {skill.tools.length > 0 && (
                  <div className="flex gap-1 flex-wrap mt-2.5 pl-10">
                    {skill.tools.map((t) => (
                      <span
                        key={t}
                        className="text-[11px] bg-base-800 text-base-500 px-1.5 py-0.5 rounded-md font-mono"
                      >
                        {t}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
