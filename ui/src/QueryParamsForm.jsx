import { Trans, useTranslation } from "react-i18next";
import styles from "./QueryParamsForm.module.css";

// One labelled input per $N placeholder. Rendered inside the caller's <form>,
// so Enter in any of these inputs submits it.
export default function QueryParamsForm({ placeholders, values, onChange }) {
  const { t } = useTranslation("queries");
  if (placeholders.length === 0) return null;

  return (
    <fieldset className={styles.panel}>
      <legend className="sr-only">{t("params.legend")}</legend>
      <p className={styles.note}>
        <Trans t={t} i18nKey="params.note" components={{ code: <code /> }} />
      </p>
      <div className={styles.fields}>
        {placeholders.map((n) => (
          <label key={n} className={styles.param}>
            <span className="sr-only">{`${t("params.valueFor")} `}</span>${n}
            <input
              type="text"
              className={styles.input}
              value={values[n] ?? ""}
              onChange={(e) => onChange(n, e.target.value)}
            />
          </label>
        ))}
      </div>
    </fieldset>
  );
}
