// A small utility symbol that takes the text color it sits in.

import { ICON_PATH, KIND_LABEL, type UtilityKind } from "@/lib/utilityIcons";

export default function UtilityIcon({ kind, title }: { kind: UtilityKind; title?: string }) {
  return (
    <svg className="uicon" viewBox="0 0 24 24" role="img" aria-label={title ?? KIND_LABEL[kind]}>
      <path d={ICON_PATH[kind]} fill="currentColor" fillRule="evenodd" />
    </svg>
  );
}
