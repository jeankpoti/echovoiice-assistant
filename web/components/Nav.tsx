"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Talk" },
  { href: "/translate", label: "Translate" },
  { href: "/learn", label: "Learn" },
];

export default function Nav() {
  const pathname = usePathname();
  return (
    <header className="site">
      <div className="brand">
        <h1>EchoVoiice</h1>
        <p className="sub">Ƒo nu kplim le Eʋegbe · Talk with me in Ewe</p>
      </div>
      <nav className="tabs" aria-label="Sections">
        {LINKS.map(({ href, label }) => (
          <Link key={href} href={href} aria-current={pathname === href ? "page" : undefined}>
            {label}
          </Link>
        ))}
      </nav>
    </header>
  );
}
