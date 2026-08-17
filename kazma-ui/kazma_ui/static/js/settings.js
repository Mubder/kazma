/**
 * Settings Alpine factory — composes tab mixins.
 * Deep-link: /settings?tab=packages
 *
 * Mixins (load before this file):
 *   settings_core.js, settings_hub.js, settings_agent.js,
 *   settings_integrations.js, settings_ops.js
 */
function settingsApp() {
    var bag = (typeof window !== "undefined" && window.KazmaSettingsMixins) || {};
    var names = ["core", "hub", "agent", "integrations", "ops"];
    var parts = names.map(function (n) {
        return typeof bag[n] === "function" ? bag[n]() : {};
    });
    return Object.assign.apply(Object, [{}].concat(parts));
}
if (typeof window !== "undefined") {
    window.settingsApp = settingsApp;
}
