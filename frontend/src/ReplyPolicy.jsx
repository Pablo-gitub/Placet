import React from "react";
import { useTranslation } from "react-i18next";

/**
 * RFC-16: whether replies on a relation return without review. In a creation
 * form `reply` is null until the user touches it, so it follows the outbound
 * policy; afterwards it is the user's own choice.
 */
export function ReplyToggle({ approval, reply, onChange, name }) {
  const { t } = useTranslation();
  const effective = reply ?? approval;
  return (
    <label className="option-item">
      <input type="checkbox" name={name} data-testid={`${name}-toggle`} checked={effective === "automatic"}
        onChange={(e) => onChange(e.target.checked ? "automatic" : "manual")} />
      <span>{t("relations.replyAutomatic")}</span>
    </label>
  );
}

/** The reply policy of an existing relation, saved on change. */
export function ReplySelect({ rel, onChange }) {
  const { t } = useTranslation();
  return (
    <select className="session-select relation-approval-select" aria-label={t("relations.replyPolicy")}
      value={rel.reply_approval || "manual"} data-testid={`relation-reply-${rel.id}`}
      onChange={(e) => onChange(e.target.value)}>
      <option value="automatic">{t("relations.replyAutomaticShort")}</option>
      <option value="manual">{t("relations.replyManualShort")}</option>
    </select>
  );
}
