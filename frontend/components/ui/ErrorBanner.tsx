export default function ErrorBanner({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    // Tonal, not bordered: error-container IS the signal, the same way every
    // other surface in this app carries its meaning in its tone.
    <div
      role="alert"
      className="rounded-md bg-error-container px-4 py-3 text-body text-on-error-container"
    >
      {message}
    </div>
  );
}
