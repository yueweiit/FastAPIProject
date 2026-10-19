/* Batch business timestamps retain their wall-clock values. */
(() => {
    'use strict';
    // MySQL DateTime/API 是无时区的业务时间；不能经过 Date/UTC 转换。
    const batchDateTimeInput = value => String(value || '').slice(0, 16);
    const batchDateTimeValue = (value, original) => {
        if (!value) return undefined;
        // datetime-local 以分钟编辑，未改日期时保留原秒数/小数秒。
        return original && value === batchDateTimeInput(original) ? original : value;
    };

    window.PageInteractions = {batchDateTimeInput, batchDateTimeValue};
})();
