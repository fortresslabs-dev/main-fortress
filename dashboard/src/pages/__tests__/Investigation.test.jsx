import { describe, it, expect, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { renderWithRouter } from '../../test/test-utils'
import Investigation from '../Investigation'

vi.mock('../../services/api')

describe('<Investigation />', () => {
  it('renders a case header once the mocked case loads', async () => {
    renderWithRouter(<Investigation />, {
      route: '/dashboard/cases/CASE-001',
      path: '/dashboard/cases/:caseId',
    })
    // Mock returns case_id="CASE-001"
    expect(await screen.findByRole('heading', { name: /Case CASE-001/i })).toBeInTheDocument()
  })
})
