// A small "?" that explains a term in plain language on hover or keyboard focus.
export default function Help({ text }: { text: string }) {
  return (
    <span className="help" tabIndex={0} role="note" aria-label={text}>
      ?<span className="tip" aria-hidden="true">{text}</span>
    </span>
  );
}
