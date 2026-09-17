export default function HeaderHint({ text }) {
  if (!text) return null;
  return (
    <span
      title={text}
      onClick={(e) => e.stopPropagation()}
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        width: 13,
        height: 13,
        marginLeft: 5,
        borderRadius: "50%",
        border: "1px solid var(--text-muted)",
        fontSize: 9,
        fontWeight: 700,
        color: "var(--text-muted)",
        cursor: "help",
        verticalAlign: "middle",
      }}
    >
      ?
    </span>
  );
}
