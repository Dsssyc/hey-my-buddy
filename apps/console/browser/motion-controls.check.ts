import { expect, type Locator } from "@playwright/test";
import { open, settle, test } from "./preview";

const macro = "macro-11111111-1111-4111-8111-111111111111";
const codex = "codex / openai / gpt-5-codex";

async function inkSwitch(tab: Locator) {
  return tab.evaluate(async node => {
    // Radix selects a tab on pointer down. Read in the following frame so a
    // short animation cannot finish during a second browser round trip.
    node.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, button: 0 }));
    await new Promise(requestAnimationFrame);
    const style = getComputedStyle(node), ink = getComputedStyle(node, "::after");
    return {
      active: node.getAttribute("data-state"), glide: node.getAttribute("data-ink-glide"),
      animations: node.getAnimations().length, transition: style.transitionProperty,
      name: ink.animationName, duration: ink.animationDuration, transform: ink.transform,
    };
  });
}

async function chooseSegment(group: Locator) {
  return group.evaluate(async root => {
    const items = [...root.querySelectorAll<HTMLElement>('[role="radio"]')];
    const before = items.find(item => item.getAttribute("aria-checked") === "true")!;
    const target = items.find(item => item !== before && item.getAttribute("aria-disabled") !== "true" && item.offsetWidth !== before.offsetWidth)!;
    target.click();
    await new Promise(requestAnimationFrame);
    const thumb = root.querySelector<HTMLElement>('[data-slot="segmented-thumb"]')!;
    const style = getComputedStyle(thumb), itemStyle = getComputedStyle(target);
    return {
      checked: target.getAttribute("aria-checked"), sliding: root.getAttribute("data-sliding"),
      opacity: style.opacity, transition: style.transitionProperty, duration: style.transitionDuration,
      itemTransition: itemStyle.transitionProperty, itemDuration: itemStyle.transitionDuration,
      animations: root.getAnimations({ subtree: true }).length,
      before: { left: before.offsetLeft, width: before.offsetWidth },
      target: { left: target.offsetLeft, top: target.offsetTop, width: target.offsetWidth, height: target.offsetHeight },
      end: { width: thumb.style.width, height: thumb.style.height, transform: thumb.style.transform },
      // The in-flight rectangle is between the two choices, not at its target.
      drawn: { left: thumb.getBoundingClientRect().left - root.getBoundingClientRect().left - (root as HTMLElement).clientLeft, width: thumb.getBoundingClientRect().width },
    };
  });
}

for (const reducedMotion of ["no-preference", "reduce"] as const) {
  const reduce = reducedMotion === "reduce";
  test.describe(`shared controls with ${reducedMotion} motion`, () => {
    test.use({ locale: "en-US", viewport: { width: 1440, height: 960 }, reducedMotion });
    test.beforeEach(async ({ page }) => {
      await page.addInitScript(() => localStorage.setItem("hey-my-buddy.console.theme", "dark"));
    });

    test("switches header navigation ink and returns to its exact resting geometry", async ({ page }) => {
      await open(page, `#tasks/${macro}`);
      const nav = page.locator("header nav");
      const target = nav.locator('a[href="#buddies"]');
      await expect(target).toBeVisible();
      await settle(page);
      expect(await nav.locator("[data-ink-glide]").count()).toBe(0);
      const motion = await target.evaluate(async node => {
        (node as HTMLElement).click();
        await new Promise(requestAnimationFrame);
        const style = getComputedStyle(node), ink = getComputedStyle(node, "::after");
        return {
          current: node.getAttribute("aria-current"), glide: node.getAttribute("data-ink-glide"),
          animations: node.getAnimations().length, transition: style.transitionProperty,
          name: ink.animationName, duration: ink.animationDuration, transform: ink.transform,
        };
      });
      expect(motion.current).toBe("page");
      if (reduce) {
        expect(motion).toMatchObject({ glide: null, animations: 0, transition: "none", name: "none", transform: "none" });
      } else {
        expect(motion).toMatchObject({ glide: "run", name: "ink-glide", duration: "0.17s" });
        expect(motion.animations).toBeGreaterThan(0);
        expect(motion.transform).not.toBe("none");
      }
      await settle(page);
      await expect(target).not.toHaveAttribute("data-ink-glide");
      expect(await target.evaluate(node => {
        const after = getComputedStyle(node, "::after");
        return { height: after.height, bottom: after.bottom, transform: after.transform, dx: (node as HTMLElement).style.getPropertyValue("--ink-dx") };
      })).toEqual({ height: "2px", bottom: "0px", transform: "none", dx: "" });
    });

    test("switches line-tab ink and returns to its exact resting geometry", async ({ page }) => {
      await open(page, `#tasks/${macro}`);
      const tabs = page.locator('[data-slot="macro-filter-tabs"]');
      const target = tabs.getByRole("tab").nth(1);
      await expect(target).toBeVisible();
      await settle(page);
      expect(await tabs.locator("[data-ink-glide]").count()).toBe(0);
      const motion = await inkSwitch(target);
      expect(motion.active).toBe("active");
      if (reduce) {
        expect(motion).toMatchObject({ glide: null, animations: 0, transition: "none", name: "none", transform: "none" });
      } else {
        expect(motion).toMatchObject({ glide: "run", name: "ink-glide", duration: "0.17s" });
        expect(motion.animations).toBeGreaterThan(0);
        expect(motion.transform).not.toBe("none");
      }
      await settle(page);
      await expect(target).not.toHaveAttribute("data-ink-glide");
      expect(await target.evaluate(node => {
        const after = getComputedStyle(node, "::after");
        return { height: after.height, bottom: after.bottom, transform: after.transform, dx: (node as HTMLElement).style.getPropertyValue("--ink-dx") };
      })).toEqual({ height: "2px", bottom: "-1px", transform: "none", dx: "" });
    });

    test("hands segmented selection to the thumb only while it is sliding", async ({ page }) => {
      await open(page, "#buddies/catalog");
      await page.getByRole("button", { name: codex, exact: true }).click();
      const drawer = page.getByRole("region", { name: codex, exact: true });
      const group = drawer.locator('[data-slot="segmented"]');
      const thumb = group.locator('[data-slot="segmented-thumb"]');
      await expect(group).toHaveAttribute("data-thumb-ready", "true");
      await settle(page);
      await expect(group).toHaveCSS("height", "32px");
      await expect(thumb).toHaveCSS("opacity", "0");
      await expect(group).not.toHaveAttribute("data-sliding");
      expect(await thumb.evaluate(node => node.getAnimations().length)).toBe(0);
      const motion = await chooseSegment(group);
      expect(motion.checked).toBe("true");
      expect(motion.target.width).not.toBe(motion.before.width);
      expect(motion.target.left).not.toBe(motion.before.left);
      expect(motion.end).toEqual({ width: `${motion.target.width}px`, height: `${motion.target.height}px`, transform: `translate(${motion.target.left}px, ${motion.target.top}px)` });
      if (reduce) {
        expect(motion).toMatchObject({ sliding: null, opacity: "0", transition: "none", itemTransition: "none", animations: 0 });
      } else {
        expect(motion).toMatchObject({ sliding: "true", opacity: "1", duration: "0.15s", itemDuration: "0.15s" });
        expect(motion.transition).toBe("transform, width, height");
        expect(motion.itemTransition).toContain("color");
        expect(motion.itemTransition).not.toContain("background-color");
        expect(motion.animations).toBeGreaterThan(0);
        expect(motion.drawn.left).not.toBe(motion.target.left);
      }
      await settle(page);
      await expect(group).not.toHaveAttribute("data-sliding");
      await expect(thumb).toHaveCSS("opacity", "0");
      const selected = group.locator('[aria-checked="true"]');
      expect(await selected.evaluate(node => getComputedStyle(node).backgroundColor)).not.toBe(await group.locator('[aria-checked="false"]').first().evaluate(node => getComputedStyle(node).backgroundColor));
      expect(await thumb.evaluate(node => node.getAnimations().length)).toBe(0);
    });

    test("opens and closes list menus with a short surface animation", async ({ page }) => {
      await open(page, `#tasks/${macro}`);
      const trigger = page.locator('[data-slot="macro-sort-button"]').first();
      await page.locator('[data-slot="macro-group-heading"]').first().hover();
      await settle(page);
      const entered = await trigger.evaluate(async node => {
        node.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, button: 0, pointerType: "mouse" }));
        await new Promise(requestAnimationFrame);
        const menu = document.querySelector('[data-slot="macro-sort-menu"]')!;
        const style = getComputedStyle(menu);
        return { state: menu.getAttribute("data-state"), name: style.animationName, duration: style.animationDuration, transition: style.transitionProperty, animations: menu.getAnimations().length };
      });
      const menu = page.locator('[data-slot="macro-sort-menu"]');
      await expect(menu).toBeVisible();
      expect(entered.state).toBe("open");
      if (reduce) expect(entered).toMatchObject({ name: "none", transition: "none", animations: 0 });
      else {
        expect(entered).toMatchObject({ name: "enter", duration: "0.15s" });
        expect(entered.animations).toBeGreaterThan(0);
      }
      await settle(page);
      expect(await menu.getByRole("menuitemradio").count()).toBeGreaterThan(0);
      const closed = await menu.evaluate(async node => {
        node.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
        await new Promise(requestAnimationFrame);
        return { connected: node.isConnected, state: node.getAttribute("data-state"), name: getComputedStyle(node).animationName, duration: getComputedStyle(node).animationDuration, animations: node.getAnimations().length };
      });
      if (reduce) expect(closed).toMatchObject({ connected: false, animations: 0 });
      else expect(closed).toMatchObject({ connected: true, state: "closed", name: "exit", duration: "0.15s" });
      await expect(menu).toHaveCount(0);
    });
  });
}

test.describe("interrupted segmented motion", () => {
  test.use({ locale: "en-US", viewport: { width: 1440, height: 960 }, reducedMotion: "no-preference" });
  test("restarts at the current visible position when another choice interrupts the slide", async ({ page }) => {
    await open(page, "#buddies/catalog");
    await page.getByRole("button", { name: codex, exact: true }).click();
    const group = page.getByRole("region", { name: codex, exact: true }).locator('[data-slot="segmented"]');
    await expect(group).toHaveAttribute("data-thumb-ready", "true");
    await settle(page);
    const interrupted = await group.evaluate(async root => {
      const options = [...root.querySelectorAll<HTMLElement>('[role="radio"][aria-checked="false"]:not([aria-disabled="true"])')];
      const thumb = root.querySelector<HTMLElement>('[data-slot="segmented-thumb"]')!;
      options[0].click();
      await new Promise(requestAnimationFrame);
      await new Promise(requestAnimationFrame);
      const before = thumb.getBoundingClientRect().left - root.getBoundingClientRect().left - (root as HTMLElement).clientLeft;
      options[options.length - 1].click();
      await new Promise(requestAnimationFrame);
      const transform = thumb.getAnimations().find(animation => (animation as CSSTransition).transitionProperty === "transform");
      const frames = (transform?.effect as KeyframeEffect | undefined)?.getKeyframes();
      return {
        before, start: frames ? new DOMMatrix(String(frames[0].transform)).e : null,
        sliding: root.getAttribute("data-sliding"), opacity: getComputedStyle(thumb).opacity,
      };
    });
    expect(interrupted).toMatchObject({ sliding: "true", opacity: "1" });
    expect(interrupted.start).not.toBeNull();
    expect(interrupted.start!).toBeCloseTo(interrupted.before, 0);
    await settle(page);
    await expect(group).not.toHaveAttribute("data-sliding");
    await expect(group.locator('[data-slot="segmented-thumb"]')).toHaveCSS("opacity", "0");
  });
});
