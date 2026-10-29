// Header logo mark that switches with the site's theme.
// Assumes Tailwind dark mode via a "dark" class on <html> (darkMode: "class").
// If your theme toggle uses data-theme instead, swap the classes for your own selectors.
export default function LogoMark({ size = 44 }: { size?: number }) {
  return (
    <span style={{ width: size, height: size, display: "inline-block", flexShrink: 0 }}>
      <img src="/quertex-mark-light.svg" alt="BRACU QUERTEX" width={size} height={size} className="block dark:hidden" />
      <img src="/quertex-mark-dark.svg" alt="BRACU QUERTEX" width={size} height={size} className="hidden dark:block" />
    </span>
  );
}
