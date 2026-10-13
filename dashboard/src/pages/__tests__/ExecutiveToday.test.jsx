import { describe, it, expect, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { renderWithRouter } from '../../test/test-utils'
import ExecutiveToday from '../ExecutiveToday'

vi.mock('../../services/api')

describe('<ExecutiveToday />', () => {
  it('renders the executive today header (CCO role injected)', async () => {
    renderWithRouter(<ExecutiveToday />)
    expect(await screen.findByRole('heading', { name: /Executive — Today/i })).toBeInTheDocument()
  })
})
