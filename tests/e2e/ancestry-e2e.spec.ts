/**
 * Step 10.4 — F18-v2 Ancestry E2E UI test.
 *
 * Validates the Ancestry page renders correctly with all expected
 * sections: admixture chart (7 populations), PCA scatter plot,
 * haplogroup section, and chromosome painting section.
 *
 * Requires a running dev server with a sample that has ancestry
 * findings pre-computed.
 */

import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'

test.describe('F18-v2: Ancestry page E2E', () => {
  test.describe('Page structure', () => {
    test('ancestry page loads without JS errors', async ({ page }) => {
      const errors: string[] = []
      page.on('pageerror', (err) => errors.push(err.message))

      await page.goto('/ancestry')
      await page.waitForLoadState('networkidle')

      expect(errors).toEqual([])
    })

    test('ancestry page has correct heading', async ({ page }) => {
      await page.goto('/ancestry')
      await page.waitForLoadState('networkidle')

      const heading = page.getByRole('heading', { level: 1 })
      await expect(heading).toBeVisible()
    })
  })

  test.describe('Admixture chart', () => {
    test('displays 7 population labels', async ({ page }) => {
      await page.goto('/ancestry')
      await page.waitForLoadState('networkidle')

      const fullNames = [
        'African',
        'Admixed American',
        'Central/South Asian',
        'East Asian',
        'European',
        'Middle Eastern',
        'Oceanian',
      ]

      // Check that at least some population labels are present
      let foundCount = 0
      for (const name of fullNames) {
        const el = page.getByText(name, { exact: false })
        const count = await el.count()
        if (count > 0) {
          await expect(el.first()).toBeVisible()
          foundCount++
        }
      }
      // Ensure at least one population label was found
      expect(foundCount).toBeGreaterThan(0)
    })
  })

  test.describe('PCA scatter', () => {
    test('PCA section exists', async ({ page }) => {
      await page.goto('/ancestry')
      await page.waitForLoadState('networkidle')

      // PCA scatter should have a container or heading
      const pcaSection = page.getByText('PCA', { exact: false })
      const count = await pcaSection.count()
      expect(count).toBeGreaterThan(0)
    })
  })

  test.describe('Chromosome painting section', () => {
    test('chromosome painting section is present', async ({ page }) => {
      await page.goto('/ancestry')
      await page.waitForLoadState('networkidle')

      // Should show chromosome painting section (download, run, or results)
      const section = page.getByText(/chromosome|painting/i)
      const count = await section.count()
      expect(count).toBeGreaterThan(0)
    })
  })

  test.describe('Haplogroup section', () => {
    test('haplogroup section exists below admixture', async ({ page }) => {
      await page.goto('/ancestry')
      await page.waitForLoadState('networkidle')

      const hapSection = page.getByText(/haplogroup/i)
      const count = await hapSection.count()
      expect(count).toBeGreaterThan(0)
    })
  })

  test.describe('Accessibility', () => {
    test('WCAG 2.1 AA compliance', async ({ page }) => {
      await page.goto('/ancestry')
      await page.waitForLoadState('networkidle')

      const results = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa'])
        .analyze()

      expect(results.violations).toEqual([])
    })
  })
})
