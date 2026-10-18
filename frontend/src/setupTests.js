import '@testing-library/jest-dom';

import { beforeEach } from "vitest";
import i18n from "./i18n";

// Existing product scenarios explicitly use Italian; locale tests opt into English.
beforeEach(async () => { await i18n.changeLanguage("it"); });
