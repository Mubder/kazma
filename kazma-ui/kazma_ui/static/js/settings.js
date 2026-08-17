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
    var app = Object.assign.apply(Object, [{}].concat(parts));
    // Alpine x-init can lose `this` after the first await on mixin methods
    // (soft-nav initTree path). Capture the component and always clear
    // loading on the same object the template reads.
    var innerInit = app.init;
    app.init = async function () {
        var self = this;
        self.loading = true;
        try {
            if (typeof innerInit === "function") {
                await innerInit.call(self);
            }
        } catch (e) {
            console.error("[Settings] Init failed:", e);
        } finally {
            self.loading = false;
        }
    };
    return app;
}
if (typeof window !== "undefined") {
    window.settingsApp = settingsApp;
}
