import "iwanthue/helpers";

declare module "iwanthue/helpers" {
  export function labToRgb(lab: [number, number, number]): [number, number, number];
  export function labToRgbHex(lab: [number, number, number]): string;
  export function validateRgb(rgb: [number, number, number]): boolean;
}
