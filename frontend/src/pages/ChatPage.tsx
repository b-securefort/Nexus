import { ChatWindow } from "../components/ChatWindow";
import { ConversationList } from "../components/ConversationList";
import { SkillPicker } from "../components/SkillPicker";
import { Sparkles, Settings } from "lucide-react";
import { Link } from "react-router-dom";

export function ChatPage() {
  return (
    <div className="flex h-screen bg-base-950 text-base-100">
      {/* Sidebar */}
      <div className="w-64 bg-base-950 border-r border-base-800/80 flex flex-col">
        <div className="px-4 py-4 border-b border-base-800/80">
          <div className="flex items-center gap-2.5">
            <div className="w-7 h-7 rounded-lg bg-accent/15 flex items-center justify-center">
              <Sparkles className="w-4 h-4 text-accent-light" />
            </div>
            <span className="font-semibold text-base-100 tracking-tight">Nexus</span>
          </div>
        </div>
        <div className="flex-1 overflow-hidden">
          <ConversationList />
        </div>
        <div className="px-4 py-3 border-t border-base-800/80">
          <Link
            to="/skills"
            className="flex items-center gap-2 text-sm text-base-500 hover:text-base-300 transition-colors"
          >
            <Settings className="w-4 h-4" />
            Manage Skills
          </Link>
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 flex flex-col bg-base-900">
        {/* Header */}
        <div className="h-14 border-b border-base-800/80 flex items-center px-5 gap-4">
          <SkillPicker />
        </div>

        {/* Chat */}
        <div className="flex-1 overflow-hidden">
          <ChatWindow />
        </div>
      </div>
    </div>
  );
}
