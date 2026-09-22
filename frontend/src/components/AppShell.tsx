import Link from "next/link";

type BackendStatus = "checking" | "online" | "offline";

export function AppShell({
  children,
  active,
  backend,
  backendMessage,
}: {
  children: React.ReactNode;
  active: "call" | "practice";
  backend: BackendStatus;
  backendMessage: string;
}) {
  return (
    <main className="relative flex min-h-screen flex-1 overflow-hidden">
      <div
        aria-hidden
        className="drift pointer-events-none absolute -left-24 top-[-10%] h-[70vh] w-[70vh] rounded-full bg-[radial-gradient(circle,rgba(184,242,74,0.22)_0%,transparent_68%)]"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute right-[-10%] bottom-[-20%] h-[55vh] w-[55vh] rounded-full bg-[radial-gradient(circle,rgba(255,107,61,0.16)_0%,transparent_70%)]"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-[linear-gradient(160deg,#0c1210_0%,#101a15_45%,#0c1210_100%)]"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 opacity-[0.07] [background-image:linear-gradient(rgba(232,242,236,0.5)_1px,transparent_1px),linear-gradient(90deg,rgba(232,242,236,0.5)_1px,transparent_1px)] [background-size:48px_48px]"
      />

      <div className="relative z-10 mx-auto flex w-full max-w-5xl flex-col px-6 py-10 sm:px-10 sm:py-14">
        <header className="animate-rise flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <span className="relative flex h-3 w-3">
              <span
                className={`pulse-ring absolute inline-flex h-full w-full rounded-full ${
                  backend === "online" ? "bg-volt" : "bg-ember"
                }`}
              />
              <span
                className={`relative inline-flex h-3 w-3 rounded-full ${
                  backend === "online" ? "bg-volt" : "bg-ember"
                }`}
              />
            </span>
            <p className="text-sm font-medium tracking-wide text-fog">
              {backend === "checking" && "Checking backend…"}
              {backend === "online" && (backendMessage || "Backend online")}
              {backend === "offline" && backendMessage}
            </p>
          </div>
          <nav className="flex items-center gap-1 rounded-full border border-line bg-ink-soft/80 p-1 text-sm">
            <Link
              href="/"
              className={`rounded-full px-4 py-1.5 font-semibold transition ${
                active === "call"
                  ? "bg-volt text-ink"
                  : "text-fog hover:text-mist"
              }`}
            >
              Live call
            </Link>
            <Link
              href="/practice"
              className={`rounded-full px-4 py-1.5 font-semibold transition ${
                active === "practice"
                  ? "bg-volt text-ink"
                  : "text-fog hover:text-mist"
              }`}
            >
              Practice chat
            </Link>
          </nav>
        </header>
        {children}
      </div>
    </main>
  );
}
