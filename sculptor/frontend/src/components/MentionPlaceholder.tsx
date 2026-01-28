import styles from "./MentionPlaceholder.module.scss";

export const createMentionPlaceholder = (
  suggestionSubject: string,
): {
  createPlaceholder: (query: string, containerElement: HTMLElement, coords: { left: number; top: number }) => void;
  cleanup: () => void;
} => {
  let placeholderElement: HTMLElement | null = null;

  const createPlaceholder = (
    query: string,
    containerElement: HTMLElement,
    coords: { left: number; top: number },
  ): void => {
    if (query === "" && !placeholderElement) {
      placeholderElement = document.createElement("span");
      placeholderElement.textContent = `search ${suggestionSubject}`;
      placeholderElement.className = styles.placeholder;

      placeholderElement.style.left = `${coords.left}px`;
      placeholderElement.style.top = `${coords.top}px`;

      containerElement.appendChild(placeholderElement);
    } else if (query !== "" && placeholderElement) {
      placeholderElement.remove();
      placeholderElement = null;
    }
  };

  const cleanup = (): void => {
    if (placeholderElement) {
      placeholderElement.remove();
      placeholderElement = null;
    }
  };

  return { createPlaceholder, cleanup };
};
