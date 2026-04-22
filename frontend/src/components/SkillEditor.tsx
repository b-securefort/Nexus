import { useState, useEffect } from "react";
import { fetchTools, createPersonalSkill, updatePersonalSkill, fetchPersonalSkill } from "../api/skills";
import type { ToolInfo, CreateSkillRequest } from "../types";
import { Save, X, AlertCircle } from "lucide-react";

interface Props {
  skillName?: string | null; // null = create mode, string = edit mode
  onSaved: () => void;
  onCancel: () => void;
}

export function SkillEditor({ skillName, onSaved, onCancel }: Props) {
  const isEdit = !!skillName;
  const [name, setName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [description, setDescription] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [selectedTools, setSelectedTools] = useState<string[]>([]);
  const [availableTools, setAvailableTools] = useState<ToolInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    fetchTools()
      .then(setAvailableTools)
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (skillName) {
      fetchPersonalSkill(skillName)
        .then((skill) => {
          setName(skill.name);
          setDisplayName(skill.display_name);
          setDescription(skill.description);
          setSystemPrompt(skill.system_prompt || "");
          setSelectedTools(skill.tools);
        })
        .catch(() => setError("Failed to load skill"));
    }
  }, [skillName]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSaving(true);

    try {
      if (isEdit) {
        await updatePersonalSkill(skillName!, {
          display_name: displayName,
          description,
          system_prompt: systemPrompt,
          tools: selectedTools,
        });
      } else {
        await createPersonalSkill({
          name,
          display_name: displayName,
          description,
          system_prompt: systemPrompt,
          tools: selectedTools,
        });
      }
      onSaved();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const toggleTool = (toolName: string) => {
    setSelectedTools((prev) =>
      prev.includes(toolName)
        ? prev.filter((t) => t !== toolName)
        : [...prev, toolName]
    );
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <h2 className="text-lg font-semibold text-base-100">
        {isEdit ? "Edit Skill" : "Create Skill"}
      </h2>

      {error && (
        <div className="flex items-center gap-2 bg-red-950/40 border border-red-800/40 rounded-xl px-3 py-2.5 text-sm text-red-300">
          <AlertCircle className="w-4 h-4 flex-shrink-0" />
          {error}
        </div>
      )}

      {/* Name */}
      <div>
        <label className="block text-sm font-medium text-base-400 mb-1">
          Name (slug)
        </label>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={isEdit}
          placeholder="my-architect"
          pattern="^[a-z0-9][a-z0-9-]{0,63}$"
          required
          className="w-full bg-base-800/60 border border-base-700/60 rounded-xl px-3 py-2.5 text-base-100 placeholder-base-600 focus:outline-none focus:ring-1 focus:ring-accent/50 focus:border-accent/40 disabled:opacity-40 text-sm"
        />
        <p className="text-xs text-base-600 mt-1.5">
          Lowercase alphanumeric + hyphens, 1-64 chars
        </p>
      </div>

      {/* Display name */}
      <div>
        <label className="block text-sm font-medium text-base-400 mb-1">
          Display Name
        </label>
        <input
          type="text"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          placeholder="My Architect"
          maxLength={100}
          required
          className="w-full bg-base-800/60 border border-base-700/60 rounded-xl px-3 py-2.5 text-base-100 placeholder-base-600 focus:outline-none focus:ring-1 focus:ring-accent/50 focus:border-accent/40 text-sm"
        />
      </div>

      {/* Description */}
      <div>
        <label className="block text-sm font-medium text-base-400 mb-1">
          Description
        </label>
        <input
          type="text"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="A brief description of what this skill does"
          maxLength={500}
          className="w-full bg-base-800/60 border border-base-700/60 rounded-xl px-3 py-2.5 text-base-100 placeholder-base-600 focus:outline-none focus:ring-1 focus:ring-accent/50 focus:border-accent/40 text-sm"
        />
      </div>

      {/* Tools */}
      <div>
        <label className="block text-sm font-medium text-base-400 mb-2">
          Tools
        </label>
        <div className="space-y-2">
          {availableTools.map((tool) => (
            <label
              key={tool.name}
              className="flex items-start gap-2 cursor-pointer"
            >
              <input
                type="checkbox"
                checked={selectedTools.includes(tool.name)}
                onChange={() => toggleTool(tool.name)}
                className="mt-1 rounded border-base-600 bg-base-800 text-accent focus:ring-accent"
              />
              <div>
                <span className="text-sm text-base-200 font-mono">
                  {tool.name}
                </span>
                {tool.requires_approval && (
                  <span className="ml-2 text-xs bg-amber-900/50 text-amber-400 px-1.5 py-0.5 rounded">
                    requires approval
                  </span>
                )}
                <p className="text-xs text-base-500">{tool.description}</p>
              </div>
            </label>
          ))}
        </div>
      </div>

      {/* System prompt */}
      <div>
        <label className="block text-sm font-medium text-base-400 mb-1">
          System Prompt
        </label>
        <p className="text-xs text-base-500 mb-2">
          Tip: draft in your editor of choice, paste here
        </p>
        <textarea
          value={systemPrompt}
          onChange={(e) => setSystemPrompt(e.target.value)}
          placeholder="You are a..."
          maxLength={32000}
          required
          rows={15}
          className="w-full bg-base-800/60 border border-base-700/60 rounded-xl px-3 py-2.5 text-base-100 placeholder-base-600 focus:outline-none focus:ring-1 focus:ring-accent/50 focus:border-accent/40 font-mono text-sm resize-y"
          style={{ minHeight: "400px" }}
        />
      </div>

      {/* Actions */}
      <div className="flex gap-3 pt-2">
        <button
          type="submit"
          disabled={saving}
          className="flex items-center gap-2 bg-accent hover:bg-accent-hover disabled:bg-base-800 disabled:text-base-600 text-white px-4 py-2 rounded-xl transition-[background-color,transform] duration-150 text-sm font-medium"
        >
          <Save className="w-4 h-4" />
          {saving ? "Saving..." : "Save"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="flex items-center gap-2 bg-base-800 hover:bg-base-700 text-base-300 px-4 py-2 rounded-xl transition-[background-color,transform] duration-150 text-sm"
        >
          <X className="w-4 h-4" />
          Cancel
        </button>
      </div>
    </form>
  );
}
