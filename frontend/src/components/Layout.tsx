import { Outlet } from "react-router-dom";
import Sidebar from "./Sidebar";
import Header from "./Header";

export default function Layout() {
  return (
    <div className="min-h-screen bg-bg-primary">
      <Sidebar />

      {/* Main content area — offset for sidebar on desktop, bottom padding on mobile */}
      <div className="md:ml-64 pb-16 md:pb-0 min-h-screen flex flex-col">
        <Header />
        <main className="flex-1 p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
