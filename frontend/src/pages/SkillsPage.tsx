import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, Plus, Pencil, Trash2, Sparkles, User } from "lucide-react";
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
      <div className="min-h-screen bg-zinc-900 text-zinc-100">
        <div className="max-w-2xl mx-auto px-4 py-8">
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
    <div className="min-h-screen bg-zinc-900 text-zinc-100">
      <div className="max-w-3xl mx-auto px-4 py-8">
        {/* Header */}
        <div className="flex items-center gap-4 mb-8">
          <Link
            to="/"
            className="text-zinc-400 hover:text-zinc-200 transition-colors"
          >
            <ArrowLeft className="w-5 h-5" />
          </Link>
          <h1 className="text-2xl font-bold">Skills</h1>
          <button
            onClick={() => setEditing("")}
            className="ml-auto flex items-center gap-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg px-4 py-2 text-sm font-medium transition-colors"
          >
            <Plus className="w-4 h-4" />
            New Skill
          </button>
        </div>

        {/* Shared skills */}
        <section className="mb-8">
          <h2 className="text-sm font-medium text-zinc-500 uppercase tracking-wider mb-3">
            Shared (Team)
          </h2>
          {sharedSkills.length === 0 && (
            <p className="text-zinc-500 text-sm">No shared skills available.</p>
          )}
          <div className="space-y-2">
            {sharedSkills.map((skill) => (
              <div
                key={skill.id}
                className="bg-zinc-800 border border-zinc-700 rounded-lg px-4 py-3 flex items-center gap-3"
              >
                <Sparkles className="w-4 h-4 text-blue-400 flex-shrink-0" />
                <div className="flex-1">
                  <div className="text-sm font-medium text-zinc-200">
                    {skill.display_name}
                  </div>
                  {skill.description && (
                    <div className="text-xs text-zinc-500 mt-0.5">
                      {skill.description}
                    </div>
                  )}
                </div>
                <div className="flex gap-1">
                  {skill.tools.map((t) => (
                    <span
                      key={t}
                      className="text-xs bg-zinc-700 text-zinc-400 px-1.5 py-0.5 rounded font-mono"
                    >
                      {t}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* Personal skills */}
        <section>
          <h2 className="text-sm font-medium text-zinc-500 uppercase tracking-wider mb-3">
            My Skills
          </h2>
          {personalSkills.length === 0 && (
            <p className="text-zinc-500 text-sm">
              No personal skills yet. Click "New Skill" to create one.
            </p>
          )}
          <div className="space-y-2">
            {personalSkills.map((skill) => (
              <div
                key={skill.id}
                className="bg-zinc-800 border border-zinc-700 rounded-lg px-4 py-3 flex items-center gap-3"
              >
                <User className="w-4 h-4 text-green-400 flex-shrink-0" />
                <div className="flex-1">
                  <div className="text-sm font-medium text-zinc-200">
                    {skill.display_name}
                  </div>
                  {skill.description && (
                    <div className="text-xs text-zinc-500 mt-0.5">
                      {skill.description}
                    </div>
                  )}
                </div>
                <div className="flex gap-1 mr-2">
                  {skill.tools.map((t) => (
                    <span
                      key={t}
                      className="text-xs bg-zinc-700 text-zinc-400 px-1.5 py-0.5 rounded font-mono"
                    >
                      {t}
                    </span>
                  ))}
                </div>
                <button
                  onClick={() => setEditing(skill.name)}
                  className="text-zinc-400 hover:text-zinc-200 p-1 transition-colors"
                >
                  <Pencil className="w-4 h-4" />
                </button>
                <button
                  onClick={() => handleDelete(skill.name)}
                  className="text-zinc-400 hover:text-red-400 p-1 transition-colors"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
