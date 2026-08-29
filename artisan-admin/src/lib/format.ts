import { formatDistanceToNowStrict, format } from "date-fns";

export const rel = (iso: string) => formatDistanceToNowStrict(new Date(iso), { addSuffix: true });
export const ts = (iso: string) => format(new Date(iso), "yyyy-MM-dd HH:mm:ss");
export const tsShort = (iso: string) => format(new Date(iso), "HH:mm:ss");
export const short = (id: string, n = 8) => id.length <= n ? id : id.slice(0, n);
