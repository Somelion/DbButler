import styles from "./QueryParamsForm.module.css";

// One labelled input per $N placeholder. Rendered inside the caller's <form>,
// so Enter in any of these inputs submits it.
export default function QueryParamsForm({ placeholders, values, onChange }) {
  if (placeholders.length === 0) return null;

  return (
    <fieldset className={styles.panel}>
      <legend className="sr-only">Placeholder values</legend>
      <p className={styles.note}>
        This query has parameter placeholders — pg_stat_statements replaces literal values with these before
        storing the query. Fill in a value for each one exactly as you'd write it in SQL (e.g. <code>42</code>,{" "}
        <code>'active'</code>, <code>now()</code>) before running it.
      </p>
      <div className={styles.fields}>
        {placeholders.map((n) => (
          <label key={n} className={styles.param}>
            <span className="sr-only">Value for </span>${n}
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
