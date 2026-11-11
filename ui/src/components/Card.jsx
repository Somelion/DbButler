import { useId } from "react";
import styles from "./Card.module.css";

// The bordered panel every screen is built from. With a title it renders a
// <section> labelled by its own heading, so screen-reader users can jump
// between cards; without one it is a plain <div>.
export default function Card({
  title,
  headingLevel = 2,
  description,
  actions,
  flush = false,
  className,
  children,
  ...rest
}) {
  const headingId = useId();
  const Heading = `h${headingLevel}`;
  const Tag = title ? "section" : "div";
  const classes = [styles.card, flush && styles.flush, className].filter(Boolean).join(" ");

  return (
    <Tag className={classes} aria-labelledby={title ? headingId : undefined} {...rest}>
      {(title || actions) && (
        <div className={styles.header}>
          {title && (
            <div className={styles.heading}>
              <Heading id={headingId} className={styles.title}>
                {title}
              </Heading>
              {description && <p className={styles.description}>{description}</p>}
            </div>
          )}
          {actions && <div className={styles.actions}>{actions}</div>}
        </div>
      )}
      {children}
    </Tag>
  );
}
