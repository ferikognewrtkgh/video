export function MockBadge({ visible, compact = false }: { visible: boolean; compact?: boolean }) {
  if (!visible) return null;
  return <span className={compact ? "mock-badge compact" : "mock-badge"}>DEMO DATA / MOCK RESULT</span>;
}
