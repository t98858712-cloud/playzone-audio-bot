const { Schema, model } = require('mongoose');

const guildSchema = new Schema({
    guildId: { type: String, required: true, unique: true },
    roles: {
        admin: { type: Array, default: [] },
        support: { type: Array, default: [] },
        finance: { type: Array, default: [] }
    },
    logsChannel: { type: String, default: null }
});

module.exports = model('GuildConfig', guildSchema);
