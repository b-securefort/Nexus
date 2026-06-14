import { BrowserRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ChatPage } from "./pages/ChatPage";
import { SkillsPage } from "./pages/SkillsPage";
import { LearningsAdminPage } from "./pages/LearningsAdminPage";
import { UsersAdminPage } from "./pages/UsersAdminPage";

const queryClient = new QueryClient();

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<ChatPage />} />
          <Route path="/skills" element={<SkillsPage />} />
          <Route path="/admin/learnings" element={<LearningsAdminPage />} />
          <Route path="/admin/users" element={<UsersAdminPage />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}

export default App;

